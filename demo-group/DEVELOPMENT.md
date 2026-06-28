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

---

## 三、ACS 设计 checklist

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

## 四、邀请路径：RPC vs Inbox

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

## 五、任务与 mentions

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

## 六、与 RPC Demo 的代码对照

| 能力 | demo (RPC) | demo-group |
|------|-----------|------------|
| Partner 入口 | `add_aip_rpc_router("/rpc")` | `@app.post("/aip")` + `join_group` |
| Leader 调用 | `AipRpcClient.start_task()` | `GroupLeaderMqClient.start_task()` |
| 传输 | HTTPS JSON-RPC | RabbitMQ + 邀请用 HTTPS |
| 多 Partner | 多次 RPC 调用 | 一次广播 + mentions |
| LangChain | Partner/Leader 均集成 | 本 Demo 未集成，便于看清 MQ 流程 |

在群组 Partner 的 `_on_task_command` 中接入 LangChain Agent 的方式，与 `demo/partner/main.py` 的 `on_start` 相同，只是触发源从 RPC 变为 MQ。

---

## 七、运维与调试

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

## 八、扩展方向

1. **多 Partner**：Leader 循环 `invite_partner`，`mentions` 指定子集执行不同子任务
2. **Inbox 邀请**：Partner 增加 `start_inbox_consuming`，去掉对 HTTP 的依赖
3. **LangChain 编排**：Leader 用 Agent 决定何时建群、广播何种任务
4. **持久化群组**：不做 `dissolve_group`，用 `GroupLeader` 会话 API 管理多 `session_id`
5. **生产 MQ**：替换 `amqp://guest:guest@localhost` 为 `amqps://` + ACL（`GroupAclClient`）

更完整的 SDK API 说明见 [`../acps-sdk/acps_sdk/aip/README.md`](../acps-sdk/acps_sdk/aip/README.md) 中的「群组模式」章节。
