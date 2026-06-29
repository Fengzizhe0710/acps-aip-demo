# 群组模式开发策略

本文档说明如何基于 `demo-group/` 开发 **AIP 群组模式**智能体。建议先完成 [`../demo/DEVELOPMENT.md`](../demo/DEVELOPMENT.md) 中的 RPC 开发入门，再阅读本文。

---

## 一、何时用群组模式

| 场景 | 推荐模式 | 原因 |
|------|---------|------|
| Leader 调用单个远程工具 | **RPC**（`demo/`） | 简单、无 MQ 依赖 |
| Leader 协调多个 Partner 并行 | **群组**（`demo-group/`） | fanout 广播、统一会话 |
| Partner 需被动加入协作会话 | **群组** | 入群邀请 + MQ 成员管理 |
| 仅需请求-响应 | **RPC** | 延迟更低、运维更轻 |

群组模式 **必须** 有 Message Queue（本 Demo 用 RabbitMQ），并需在 ACS 中声明 `capabilities.messageQueue`。

---

## 二、架构分工

```text
┌─────────────────────────────────────────────────────────┐
│ Leader                                                   │
│  GroupLeaderMqClient                                     │
│    connect → create_group → start_consuming              │
│    invite_partner (HTTPS /aip)                           │
│    start_task(mentions="all") → publish TaskCommand      │
│    接收 TaskResult / GroupMgmtResult                     │
└───────────────────────────┬─────────────────────────────┘
                            │ RabbitMQ fanout
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
   Partner A           Partner B           Leader 自身队列
   GroupPartnerMqClient
```

### Leader 职责

1. **连接 MQ** 并 `create_group()` 声明 fanout exchange + 自身队列
2. **`start_consuming()`** 监听群内消息（Partner 的 TaskResult、管理消息等）
3. **`invite_partner()`** 通过 Partner ACS 中的 JSONRPC 端点（`/aip`）发送 `RabbitMQRequest`
4. **`start_task()`** 广播 `TaskCommandType.Start`，用 `mentions` 指定执行者
5. 任务结束后 **`dissolve_group()`** 清理 exchange（auto_delete 绑定解绑后自动删除）

参考实现：[`leader/main.py`](./leader/main.py)

### Partner 职责

1. 暴露 **`POST /aip`**，解析 `RabbitMQRequest`，调用 `GroupPartnerMqClient.join_group()`
2. **`set_command_handler`** 处理 `TaskCommand`；根据 `is_mentioned` 决定是否执行
3. 处理完成后 **`send_task_result()`** 广播状态（如 `TaskState.Completed`）
4. （可选）`start_inbox_consuming()` 支持 Leader 通过 AMQP inbox 发邀请，无需 RPC

参考实现：[`partner/main.py`](./partner/main.py)

### 2.1 两条通道：HTTPS 邀请 + MQ 协作

群组模式 **不是** 全程走 HTTP。实际有两条通道：

| 通道 | 协议 | 用途 | 谁发起 |
|------|------|------|--------|
| **邀请通道** | HTTPS + mTLS（`POST /aip`） | Leader 把「群组 ID、Exchange 名、MQ 地址」告诉 Partner | Leader → Partner |
| **协作通道** | RabbitMQ（AMQP） | 广播 `TaskCommand`、回传 `TaskResult`、群组管理消息 | 双方 publish / consume |

```text
阶段 1（一次性，HTTPS）          阶段 2（持续，RabbitMQ）
──────────────────────          ──────────────────────────
Leader --RabbitMQRequest-->     Leader --TaskCommand-->  fanout exchange
        Partner /aip                    │
        Partner join_group()            ├──> Partner 队列
        Partner 连 MQ、绑队列            ├──> Leader 自身队列
                                        Partner --TaskResult--> exchange
                                              └──> Leader 收到
```

**AIP 业务模型**（`TaskCommand` / `TaskResult`）与 RPC Demo 相同；**传输层**从「单次 HTTPS JSON-RPC」变为「MQ 上的 JSON 消息 + 邀请时用 HTTPS」。

### 2.2 RabbitMQ 拓扑

Leader 调用 `create_group()` 后，MQ 上会出现：

```text
Exchange（fanout，auto_delete）
  名称: group_{leader_aic}_{group_id}
  例:   group_1.2.156..._group-a1b2c3d4

Queue（每个成员一条，auto_delete）
  Leader: group_{leader_aic}_{group_id}_{leader_aic}
  Partner: group_{leader_aic}_{group_id}_{partner_aic}

绑定: 每个 Queue --bind--> Exchange（fanout 无 routing key，全员收到广播）
```

SDK 命名函数见 `acps_sdk/aip/aip_group_runtime.py`：`build_group_exchange_name`、`build_group_queue_name`。

Partner 入群时（`join_group`）会：连 RabbitMQ → 找到 Exchange → 声明自己的 Queue → bind → 开始 consume。

### 2.3 MQ 上的 AIP 消息格式

所有群组消息均为 **JSON**，通过 `model_dump()` 序列化后 publish 到 Exchange。核心 `type` 字段：

| type | 方向 | 说明 |
|------|------|------|
| `task-command` | Leader → 全员 | 任务命令（Start / Continue / Complete / Cancel / Get） |
| `task-result` | Partner → 全员 | 任务状态与产出（Working / Completed / Failed …） |
| `group-mgmt-command` | Leader → 指定成员 | 静音、退群、解散等 |
| `group-mgmt-result` | Partner → 全员 | 管理命令的执行结果 |

`TaskCommand` / `TaskResult` 字段与 RPC 模式一致，群组额外字段：

- `groupId`：当前群组
- `sessionId`：会话 ID（Leader 生成）
- `mentions`：`"all"` 或 Partner AIC 列表
- `senderId` / `senderRole`：发送方 AIC 与角色（leader / partner）

Partner 收到 `task-command` 后应调用 `command.is_mentioned(partner_aic)` 判断是否执行；Demo 使用 `mentions="all"`。

### 2.4 完整时序（对应 demo-group 启动流程）

```text
Leader                              Partner                         RabbitMQ
  │                                   │                               │
  │ connect()                         │                               │
  │ create_group() ──────────────────────────────────────────────────►│ declare exchange + leader queue
  │ start_consuming() ◄───────────────────────────────────────────────│ leader queue 开始监听
  │                                   │                               │
  │ POST /aip (RabbitMQRequest) ─────►│                               │
  │                                   │ join_group()                  │
  │                                   │ connect + declare partner queue│
  │                                   │ bind + consume ───────────────►│
  │◄── RabbitMQResponse(queueName) ───│                               │
  │                                   │                               │
  │ start_task(mentions="all") ───────────────────────────────────────►│ publish TaskCommand
  │                                   │◄── fanout ────────────────────│
  │                                   │ _on_task_command 执行业务      │
  │                                   │ send_task_result(Completed) ─►│ publish TaskResult
  │◄── _on_group_message ─────────────────────────────────────────────│
  │ dissolve_group() ─────────────────────────────────────────────────►│ 解绑后 exchange 自动删除
```

---

## 三、实际开发：Client 与 Server 怎么实现

在群组模式中，**Leader 侧主要是 MQ Client（协调者）**，**Partner 侧是 HTTPS Server + MQ Client（执行者）**。不要与 RPC Demo 的「Partner 当 HTTP Server、Leader 当 HTTP Client」简单等同——群组里 **双方都是 MQ 的生产者/消费者**，Partner 额外多一个 HTTP 端点用于接收入群邀请。

### 3.1 角色对照

| | Leader（协调者） | Partner（执行者） |
|---|---|---|
| **HTTP 角色** | 仅 **Client**（邀请时 POST Partner `/aip`） | **Server**（暴露 `POST /aip`） |
| **MQ 角色** | Client：建 Exchange、广播命令、收结果 | Client：入群后 consume 命令、publish 结果 |
| **SDK 类** | `GroupLeaderMqClient` | `GroupPartnerMqClient` |
| **证书用途** | `clientAuth`（HTTPS 邀请 + 可选 AMQPS） | `serverAuth`（HTTPS `/aip` + 可选 AMQPS） |
| **必写回调** | `set_message_handler`（收 TaskResult） | `set_command_handler`（收 TaskCommand） |
| **参考文件** | [`leader/main.py`](./leader/main.py) | [`partner/main.py`](./partner/main.py) |

### 3.2 Leader Client 实现清单

实际开发 Leader 时，按以下顺序接入 SDK：

```python
# 1. 初始化（AIC + RabbitMQ 连接参数）
client = GroupLeaderMqClient(
    leader_aic=leader_aic,
    rabbitmq_host="localhost",
    rabbitmq_port=5672,
    rabbitmq_vhost="/",
    rabbitmq_user="guest",
    rabbitmq_password="guest",
)

# 2. 连接 MQ → 建群 → 开始监听（顺序不能乱）
await client.connect()
group_id = await client.create_group(group_id="group-xxx")
client.set_message_handler(your_message_handler)  # 处理 TaskResult 等
await client.start_consuming()

# 3. 邀请 Partner（HTTPS，不是 MQ）
partner_info = await client.invite_partner(
    partner_acs=ACSObject(aic=partner_aic),
    partner_rpc_url="https://host:port/aip",  # 与 Partner ACS 中 JSONRPC 端点一致
    ssl_context=ssl_context,                  # mTLS 验 Partner + 出示 Leader 身份
    client_cert=(cert_path, key_path),
)

# 4. 广播任务
task_id = await client.start_task(
    session_id="session-xxx",
    text_content="任务内容",
    mentions="all",  # 或 [partner_aic, ...]
)

# 5. 收尾
await client.dissolve_group()
await client.close()
```

**`your_message_handler` 典型写法**（见 demo `leader/main.py` 的 `_on_group_message`）：

- 判断 `isinstance(message, TaskResult)`
- 读 `senderId` 知道哪个 Partner 回复
- 读 `status.state` / `products` 拿结果
- 终态（Completed / Failed / Canceled）时写入本地列表或唤醒等待逻辑

Leader **不需要** 自己写 RabbitMQ Exchange/Queue 声明逻辑，全部由 `GroupLeaderMqClient.create_group()` 完成。

### 3.3 Partner Server 实现清单

Partner 实际开发分 **HTTP 层** 和 **MQ 层** 两块：

#### HTTP 层：接收入群邀请

```python
app = FastAPI()
mq_client = GroupPartnerMqClient(partner_aic=partner_aic, ...)

@app.post("/aip")
async def handle_aip_invite(request: Request):
    rabbitmq_request = RabbitMQRequest.model_validate(await request.json())
    response = await mq_client.join_group(rabbitmq_request)
    # join_group 内部：连 MQ、建队列、bind、start consume
    return JSONResponse(content=response.model_dump(exclude_none=True), ...)
```

- 路径须与 ACS `endPoints` 中 JSONRPC URL 一致（Demo 为 `/aip`）
- 使用 **uvicorn + mTLS** 启动（`ssl_certfile` / `ssl_keyfile` / `ssl_ca_certs`），与 RPC Demo Partner 相同

#### MQ 层：处理群组任务

```python
async def on_task_command(command: TaskCommand, is_mentioned: bool) -> None:
    if not is_mentioned:
        return
    if command.command != TaskCommandType.Start:
        return
    # 执行业务（可接入 LangChain，写法同 demo/partner/main.py 的 on_start）
    await mq_client.send_task_result(
        task_id=command.taskId,
        session_id=command.sessionId,
        state=TaskState.Completed,
        products=[Product(..., dataItems=[TextDataItem(text=answer)])],
    )

mq_client.set_command_handler(on_task_command)
```

**注意**：`set_command_handler` 应在进程启动时注册；`join_group` 成功后会自动 `_start_consuming()`，之后收到的 `task-command` 会回调 `on_task_command`。

推荐任务状态机（生产环境）：

```text
Start + is_mentioned
  → accept_task (Accepted)
  → start_working (Working)
  → 执行业务
  → submit_for_completion (AwaitingCompletion) 或 send_task_result(Completed)
```

Demo 简化为直接 `Completed`。

### 3.4 与 RPC Demo 的代码迁移

| RPC Demo（demo/） | 群组 Demo（demo-group/） |
|---|---|
| `add_aip_rpc_router(app, "/rpc", handlers)` | `@app.post("/aip")` + `join_group()` |
| `handlers.on_start(command, task)` | `on_task_command(command, is_mentioned)` |
| `AipRpcClient(...).start_task()` | `GroupLeaderMqClient(...).start_task()` |
| 返回值经 HTTP Response | 返回值经 `send_task_result` → MQ → Leader handler |

业务逻辑（LangChain Agent、工具调用）可复用：把 RPC `on_start` 里的核心代码搬进 `on_task_command` 即可。

### 3.5 身份与安全

| 环节 | 验证方式 |
|------|---------|
| Leader 邀请 Partner | HTTPS mTLS：Leader 用 `clientAuth` 调 Partner `/aip` |
| Partner 接受邀请 | Partner 验 Leader 客户端证书（`ssl.CERT_REQUIRED`） |
| MQ 连接（本地 Demo） | `guest/guest` 明文 AMQP |
| MQ 连接（生产） | `amqps://` + `ssl_context` + EXTERNAL 认证，或 `accessToken` |

MQ 消息里的 `senderId` 是 **协议层声明**；本地 Demo 未做「MQ 连接身份 ↔ senderId」的额外绑定。生产环境应使用 AMQPS + ACL，并只处理已入群成员队列上的消息。

---

## 四、ACS 设计 checklist

### Partner ACS

```json
{
  "endPoints": [
    {
      "url": "amqp://localhost:5672/?inbox=inbox_{AIC}",
      "transport": "AMQP"
    },
    {
      "url": "https://localhost:25003/aip",
      "transport": "JSONRPC"
    }
  ],
  "capabilities": {
    "messageQueue": ["rabbitmq:>=4.2"]
  }
}
```

要点：

- `{AIC}` 占位符由 Registry / CLI 在 sync 时替换为正式 AIC
- `/aip` 路径可自定义，Leader 的 `PARTNER_AIP_URL` 须与 ACS 一致
- `serverAuth` 证书用于 Partner HTTPS 服务端

### Leader ACS

- 至少一个 **AMQP** 端点（inbox），供未来 inbox 邀请或其它 Leader 能力扩展
- 通常 **不需要** JSONRPC 服务端点（Leader 主动发起协作）
- `clientAuth` 证书用于邀请 Partner 时的 mTLS 客户端身份

---

## 五、邀请路径：RPC vs Inbox

SDK 支持两种入群邀请方式（参见 `acps-sdk` 测试 `test_group_invitation.py`）：

| 路径 | Leader 调用 | Partner 接收 | 适用 |
|------|------------|-------------|------|
| **RPC** | `GroupLeaderMqClient.invite_partner(url=.../aip)` | `POST /aip` → `join_group()` | 本 Demo；Partner 公网可达 |
| **Inbox** | `invite_via_inbox(partner_inbox=...)` | `start_inbox_consuming()` | Partner 仅暴露 MQ、无 HTTP |

**选择策略：**

- Partner 已有 HTTP 服务 → 优先 RPC（实现简单，与 `demo-group/partner/main.py` 一致）
- Partner 在内网 / 无固定 IP → Inbox（Leader 向 `inbox_{AIC}` 发 `InboxGroupInvitation`）
- ACS 同时声明 AMQP + JSONRPC 时，SDK 的 `GroupLeader` 高层 API 会 **优先 Inbox**（MQ 同机时）

---

## 六、任务与 mentions

群组内任务与 RPC 模式共用 `TaskCommand` / `TaskResult` 模型，但多了群组字段：

| 字段 | 说明 |
|------|------|
| `groupId` | 当前群组 ID |
| `sessionId` | 会话 ID，Leader 建群后自行生成 |
| `mentions` | `"all"` 或 Partner AIC 列表；Partner 应检查 `is_mentioned` |

推荐 Partner 处理流程：

```text
TaskCommandType.Start + is_mentioned
  → accept_task (可选)
  → start_working (可选)
  → 执行业务
  → send_task_result(Completed, products=...)
```

本 Demo 简化为：仅处理 `Start` + `is_mentioned`，直接 `Completed`。

---

## 七、与 RPC Demo 的代码对照

| 能力 | demo (RPC) | demo-group |
|------|-----------|------------|
| Partner 入口 | `add_aip_rpc_router("/rpc")` | `@app.post("/aip")` + `join_group` |
| Leader 调用 | `AipRpcClient.start_task()` | `GroupLeaderMqClient.start_task()` |
| 传输 | HTTPS JSON-RPC | RabbitMQ + 邀请用 HTTPS |
| 多 Partner | 多次 RPC 调用 | 一次广播 + mentions |
| LangChain | Partner/Leader 均集成 | 本 Demo 未集成，便于看清 MQ 流程 |

在群组 Partner 的 `_on_task_command` 中接入 LangChain Agent 的方式，与 `demo/partner/main.py` 的 `on_start` 相同，只是触发源从 RPC 变为 MQ。

---

## 八、运维与调试

### RabbitMQ

本地开发可用 Docker 启动（详见 [demo-group/README.md](../demo-group/README.md#启动-rabbitmq)）。

- 默认 `guest/guest` 仅适合本地；生产应使用独立 vhost、账号，或通过 **mq-auth-server** 签发 `accessToken`
- Management UI：`http://localhost:15672`（若使用 `-management` 镜像）
- 群组 exchange 命名：`group_{leader_aic}_{group_id}`（见 SDK `build_group_exchange_name`）

### 常见问题

| 现象 | 排查 |
|------|------|
| Leader 邀请失败 | Partner 是否启动；`/aip` URL 与证书；防火墙 |
| Partner 收不到任务 | 是否 `join_group` 成功；RabbitMQ 是否连通；`mentions` 是否包含该 Partner |
| Leader 收不到 TaskResult | 是否 `start_consuming()`；Partner 是否调用 `send_task_result` |
| 与 RPC Demo 端口冲突 | Partner 使用 `25003`，RPC Demo 使用 `25002` |

### 日志

SDK 使用标准 `logging`，可开启：

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

---

## 九、扩展方向

1. **多 Partner**：Leader 循环 `invite_partner`，`mentions` 指定子集执行不同子任务
2. **Inbox 邀请**：Partner 增加 `start_inbox_consuming`，去掉对 HTTP 的依赖
3. **LangChain 编排**：Leader 用 Agent 决定何时建群、广播何种任务
4. **持久化群组**：不做 `dissolve_group`，用 `GroupLeader` 会话 API 管理多 `session_id`
5. **生产 MQ**：替换 `amqp://guest:guest@localhost` 为 `amqps://` + ACL（`GroupAclClient`）

更完整的 SDK API 说明见 [`../acps-sdk/acps_sdk/aip/README.md`](../acps-sdk/acps_sdk/aip/README.md) 中的「群组模式」章节。
