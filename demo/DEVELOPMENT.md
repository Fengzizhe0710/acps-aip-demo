# 智能体开发指南

本文档说明如何基于本 Demo 开发 ACPs AIP 智能体。建议先阅读 [README.md](./README.md) 中的概念与启动说明。

---

## 一、用 LangChain 快速做一个智能体

无论 Server 还是 Client，业务逻辑层都可以用 **LangChain Agent** 组装：**大模型 + 工具 + 系统提示词**。

### 最小示例

```python
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="gpt-4o-mini",
    api_key="...",
    base_url="https://api.openai.com/v1",
    temperature=0.0,
)

tools = [my_tool_1, my_tool_2]   # 普通函数或 @tool 装饰的函数

agent = create_agent(
    model=llm,
    tools=tools,
    system_prompt="你是一个得力的助手……",
)

result = await agent.ainvoke({
    "messages": [{"role": "user", "content": "用户问题"}]
})
answer = result["messages"][-1].content
```

### 本 Demo 中的两个 Agent

| 角色 | 文件 | LangChain 用途 |
|------|------|---------------|
| Partner（Server） | `partner/main.py` | 收到 AIP 任务后，用 Agent + Tavily 搜索回答 |
| Leader（Client） | `leader/main.py` | 和用户对话，需要实时信息时调用 AIP 工具 |

Partner 在 `on_start` 里临时创建 Agent；Leader 在模块级创建 Agent 并进入多轮对话循环。

---

## 二、Server 侧开发（AIP RPC 服务端）

Server 侧智能体对外暴露 **AIP RPC 接口**，核心是实现 **`on_start` 回调**。

### 2.1 整体结构（参考 `partner/main.py`）

```text
1. 读取配置（.env）
2. 加载 mTLS 证书
3. 定义 on_start（业务逻辑）
4. 注册 AIP RPC 路由（/rpc）
5. 启动 uvicorn（HTTPS + mTLS）
```

### 2.2 注册 RPC 路由

```python
from acps_sdk.aip.aip_rpc_server import CommandHandlers, add_aip_rpc_router
from fastapi import FastAPI

handlers = CommandHandlers(on_start=on_start)
app = FastAPI()
add_aip_rpc_router(app, "/rpc", handlers)
```

Leader 调用 `start` 命令时，SDK 会触发你注册的 `on_start`。

### 2.3 前置知识：AIP 数据结构

#### TaskCommand（Leader → Partner）

Leader 发来的任务命令，常用字段：

| 字段 | 说明 |
|------|------|
| `taskId` | 任务 ID |
| `sessionId` | 会话 ID |
| `command` | 命令类型，如 `start` |
| `dataItems` | 输入数据，通常是 `[TextDataItem(text="用户问题")]` |

#### TaskResult（Partner → Leader）

Partner 返回的任务结果，常用字段：

| 字段 | 说明 |
|------|------|
| `taskId` | 任务 ID |
| `status` | `TaskStatus`，含 `state`（任务状态） |
| `products` | 产出物列表，答案放在这里 |

#### Product / TextDataItem

```python
from acps_sdk.aip import Product, TextDataItem

product = Product(
    id=f"prod-{command.taskId}",
    dataItems=[TextDataItem(text="这是答案")],
)
task_result.products = [product]
```

Leader 侧通过 `task.products[0].dataItems[0].text` 读取答案。

### 2.4 前置知识：Task 状态与转移

```text
                    start 命令
                        │
                        ▼
                   ┌─────────┐
                   │ Accepted │  已接受
                   └────┬────┘
                        │
                        ▼
                   ┌─────────┐
                   │ Working  │  处理中（可选，长时间任务时使用）
                   └────┬────┘
                        │
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
   ┌────────────┐ ┌───────────┐ ┌──────────┐
   │ Awaiting   │ │ Completed │ │ Failed / │
   │ Completion │ │  已完成    │ │ Rejected │
   └─────┬──────┘ └───────────┘ └──────────┘
         │
    complete 命令
         │
         ▼
   ┌───────────┐
   │ Completed │
   └───────────┘
```

| 状态 | 含义 | Demo 中的情况 |
|------|------|--------------|
| `Accepted` | 已接受任务 | SDK 收到 start 后默认 |
| `Working` | 正在处理 | 耗时任务时可主动更新 |
| `AwaitingCompletion` | 结果已就绪，等 Leader 确认 | 需 Leader 调 `complete` |
| `Completed` | 已完成 | **本 Demo Partner 直接返回此状态** |
| `Failed` / `Rejected` / `Canceled` | 失败 / 拒绝 / 取消 | 出错或主动取消时 |

本 Demo 的 Partner 在 `on_start` 里**同步处理完**并直接返回 `Completed`，适合快速演示。  
生产环境中，长时间任务可先返回 `Working`，处理完再更新为 `AwaitingCompletion` 或 `Completed`。

### 2.5 实现 on_start（本 Demo 核心）

```python
async def on_start(command: TaskCommand, task: TaskResult | None) -> TaskResult:
    # 1. 取出 Leader 发来的问题
    user_input = command.dataItems[0].text

    # 2. 执行业务逻辑（LangChain Agent、数据库、API 等）
    answer = await my_business_logic(user_input)

    # 3. 包装成 AIP 格式返回
    new_task = TaskManager.create_task(command, initial_state=TaskState.Completed)
    new_task.products = [
        Product(id=f"prod-{command.taskId}", dataItems=[TextDataItem(text=answer)])
    ]
    return new_task
```

完整实现见 [`partner/main.py`](./partner/main.py)。

### 2.6 mTLS 服务端配置

```python
cert_file, key_file, ca_file = _load_mtls(ROOT)

uvicorn.run(
    app,
    host="0.0.0.0",
    port=RPC_PORT,
    ssl_certfile=str(cert_file),      # Partner 公钥证书
    ssl_keyfile=str(key_file),        # Partner 私钥
    ssl_ca_certs=str(ca_file),        # 用于验证 Leader 客户端证书
    ssl_cert_reqs=ssl.CERT_REQUIRED, # 必须双向认证
)
```

证书目录约定：

```text
acps-atr/certs/{aic}.crt   # 公钥
acps-atr/certs/ca.crt      # CA 根证书
acps-atr/private/{aic}.key # 私钥
```

---

## 三、Client 侧开发（AIP RPC 客户端）

Client 侧智能体通过 **`AipRpcClient`** 调用远程 Partner，通常包装成 **LangChain 工具**（`@tool`）。

### 3.1 整体结构（参考 `leader/main.py`）

```text
1. 读取配置
2. 加载 mTLS 客户端证书
3. 用 @tool 定义 query_xxx 工具（内部用 AipRpcClient）
4. 创建 LangChain Agent
5. 多轮对话循环
```

### 3.2 用 @tool 包装 AIP 调用

```python
from langchain.tools import tool
from acps_sdk.aip import AipRpcClient, TaskState

@tool
async def query_realtime_agent(query: str) -> str:
    """调用远程实时信息智能体，获取最新的股价、天气、新闻等。"""
    client = AipRpcClient(
        partner_url=PARTNER_RPC_URL,
        leader_id=_leader_aic,
        ssl_context=_ssl_context,   # mTLS 客户端上下文
    )
    try:
        task = await client.start_task(
            session_id=f"session-{uuid.uuid4()}",
            task_id=f"task-{uuid.uuid4()}",
            user_input=query,
        )
        # 等待 Partner 处理完成
        while task.status.state in (TaskState.Accepted, TaskState.Working):
            await asyncio.sleep(0.2)
            task = await client.get_task(task_id=task.taskId, session_id=...)

        if task.status.state == TaskState.AwaitingCompletion:
            await client.complete_task(task_id=task.taskId, session_id=...)

        return task.products[0].dataItems[0].text
    finally:
        await client.close()
```

完整实现见 [`leader/main.py`](./leader/main.py)。

### 3.3 创建 Agent 并对话

```python
from langchain.agents import create_agent

agent = create_agent(model=llm, tools=[query_realtime_agent], system_prompt=SYSTEM_PROMPT)

messages = []
messages.append({"role": "user", "content": user_input})
result = await agent.ainvoke({"messages": messages})
reply = result["messages"][-1].content
```

LangChain 会自动判断何时调用 `query_realtime_agent`，无需手写 tool call 解析。

### 3.4 进阶：通过 ADP 发现智能体再连接

本 Demo 的 Leader **硬编码**了 Partner 地址（`https://localhost:{RPC_PORT}/rpc`）。  
生产环境中，应通过 **ADP（Agent Discovery Protocol）** 动态发现 Partner。

#### 流程

```text
1. 向 Discovery Service 发起查询（按 skill、tag、aic 等过滤）
2. 从返回的 acsMap 中读取 Partner 的 ACS
3. 从 ACS.endPoints 取 RPC URL 和 security 配置
4. 用对应 mTLS 证书建立 AipRpcClient 连接
```

#### Step 1：Discovery 查询（CLI 示例）

```bash
cd ../acps-cli

uv run acps-cli discover query \
  --type filtered \
  --filter-json '{
    "conditions": [
      {"field": "skills.tags", "op": "any_of", "value": ["realtime"]},
      {"field": "active", "op": "eq", "value": true}
    ]
  }'
```

返回 JSON 中的 `result.acsMap` 以 AIC 为 key，value 即完整 ACS。  
`result.agents` 列出匹配的智能体技能及排序。

#### Step 2：从 ACS 解析连接信息

```python
import json

# 假设从 Discovery 响应中取到 partner 的 ACS
acs = discovery_result["acsMap"]["demo-partner-aic"]

# 取 RPC 端点
endpoint = acs["endPoints"][0]
partner_url = endpoint["url"]                    # 如 https://partner.example.com/rpc
uses_mtls = "mtls" in endpoint.get("security", [{}])[0]

# 取 AIC（用于 leader_id 和证书文件名）
partner_aic = acs["aic"]
```

#### Step 3：动态创建 Client 并调用

```python
from acps_sdk.adp import DiscoveryRequest, DiscoveryFilter, FilterCondition, FilterOperator

# SDK 提供 ADP 请求/响应模型，HTTP 调用 Discovery API 示例：
# POST {DISCOVERY_URL}/discover
# Body: DiscoveryRequest(...).to_dict()

@tool
async def query_discovered_agent(query: str) -> str:
    """通过 ADP 发现实时信息智能体并调用。"""
    # 1. 查询 Discovery（httpx / requests 调用 Discovery API）
    #    filter: skills.tags contains "realtime"
    #
    # 2. 从 acsMap 取 Partner ACS，解析 endPoints[0].url
    #
    # 3. 加载 Leader mTLS 证书，创建 AipRpcClient
    #
    # 4. start_task → 轮询 → 取 products[0].dataItems[0].text

    partner_url = "..."   # 从 Discovery ACS 动态获取
    client = AipRpcClient(
        partner_url=partner_url,
        leader_id=_leader_aic,
        ssl_context=_ssl_context,
    )
    # ... 同 query_realtime_agent
```

#### ADP SDK 模型参考

```python
from acps_sdk.adp import (
    DiscoveryRequest,
    DiscoveryFilter,
    FilterCondition,
    FilterOperator,
    DiscoveryResponse,
)

request = DiscoveryRequest(
    type="filtered",
    filter=DiscoveryFilter(conditions=[
        FilterCondition(field="skills.tags", op=FilterOperator.ANY_OF, value=["realtime"]),
        FilterCondition(field="active", op=FilterOperator.EQ, value=True),
    ]),
    limit=5,
)
# request.to_json() 作为 Discovery API 请求体
```

Discovery 详细联调见 [`../acps-cli/README.md`](../acps-cli/README.md) 第 2.3 节。

---

## 四、开发检查清单

### Server（Partner）

- [ ] 编写 `acs.json`（aic、skills、endPoints、security）
- [ ] 通过 acps-cli 完成可信注册并获取 mTLS 证书
- [ ] 实现 `on_start`，正确处理 `TaskCommand` / 返回 `TaskResult`
- [ ] 注册 `/rpc` 路由，启用 mTLS
- [ ] 用 LangChain（或其他框架）实现业务逻辑

### Client（Leader）

- [ ] 编写 `acs.json` 并完成注册发证
- [ ] 用 `@tool` 封装 `AipRpcClient` 调用
- [ ] 创建 LangChain Agent，配置 system_prompt 引导工具使用
- [ ] （可选）通过 ADP 发现 Partner，动态解析 RPC URL
- [ ] 处理 Task 状态轮询（Accepted → Working → Completed）

---

## 五、相关代码索引

| 主题 | 文件 |
|------|------|
| Partner Server | [`partner/main.py`](./partner/main.py) |
| Leader Client | [`leader/main.py`](./leader/main.py) |
| AIP 数据模型 | `acps-sdk/acps_sdk/aip/aip_base_model.py` |
| AIP RPC Server | `acps-sdk/acps_sdk/aip/aip_rpc_server.py` |
| AIP RPC Client | `acps-sdk/acps_sdk/aip/aip_rpc_client.py` |
| ADP 模型 | `acps-sdk/acps_sdk/adp/models.py` |
| acps-cli 联调 | [`../acps-cli/README.md`](../acps-cli/README.md) |
