# ACPs AIP 智能体开发 Demo

本仓库 `demo/` 是一个 **ACPs 协议体系下、支持 AIP（Agent Interaction Protocol）的智能体开发演示**。  
它用最小的 **Leader + Partner** 架构，展示智能体如何：

- 用 **ACS** 描述自身能力
- 通过 **ATR / acps-cli** 完成可信注册与证书签发
- 用 **AIP RPC + mTLS** 安全通信
- 用 **LangChain** 快速组装「本地 LLM + 远程工具」

```text
用户 ──► Leader（LangChain Agent，本地 LLM）
              │
              │  @tool query_realtime_agent
              │  AipRpcClient（mTLS）
              ▼
         Partner（LangChain Agent，联网搜索）
              ▲
              │  ADP 发现（可选）
         Discovery Service
```

## 目录结构

```text
demo/
  README.md              # 本文：概念 + 启动演示
  DEVELOPMENT.md         # 智能体开发指南（Server / Client）
  requirements.txt
  leader/
    main.py              # Client 侧：LangChain + AIP RPC 工具
    acps-atr/            # Leader 注册材料（acs.json.example → acs.json）
  partner/
    main.py              # Server 侧：on_start + AIP RPC 服务
    acps-atr/            # Partner 注册材料（acs.json.example → acs.json）
```

---



## 一、ACPs 核心概念


| 概念       | 全称                             | 一句话说明                                  |
| -------- | ------------------------------ | -------------------------------------- |
| **AIC**  | Agent Identity Code            | 智能体唯一身份标识，类似「身份证号」                     |
| **ACS**  | Agent Capability Specification | 智能体能力描述文件（`acs.json`），包含名称、技能、端点、安全方式等 |
| **AIP**  | Agent Interaction Protocol     | 智能体之间的交互协议：发任务、查状态、取结果                 |
| **ATR**  | Agent Trust Registry           | 智能体可信注册服务，负责审核、分配 AIC、对接 CA 发证         |
| **ADP**  | Agent Discovery Protocol       | 智能体发现协议，按能力/标签等检索已注册的智能体               |
| **mTLS** | Mutual TLS                     | 双向 TLS，通信双方都要出示合法证书                    |




### 它们如何配合

```text
1. 开发者编写 acs.json（ACS）
2. 通过 acps-cli 提交到 Registry（ATR），审核通过后获得正式 AIC
3. CA 根据 AIC 签发 mTLS 证书
4. Partner 按 ACS 中的 endPoints 暴露 AIP RPC 服务
5. Leader 通过 ADP 发现 Partner（或已知地址直连）
6. Leader 用 AIP Client 发 start 命令，Partner 的 on_start 处理任务并返回 Product
```



### 本 Demo 中的对应文件


| 概念         | Demo 中的位置                                                                       |
| ---------- | ------------------------------------------------------------------------------- |
| ACS        | `leader/acps-atr/acs.json`（自 `acs.json.example` 复制）、`partner/acps-atr/acs.json` |
| AIC        | `acs.json` 里的 `"aic"` 字段                                                        |
| mTLS 证书    | `acps-atr/certs/`（公钥 + CA）、`acps-atr/private/`（私钥）                              |
| AIP Server | `partner/main.py` 的 `on_start` + `/rpc`                                         |
| AIP Client | `leader/main.py` 的 `query_realtime_agent` 工具                                    |


---



## 二、启动项目演示

演示分两种方式：**完整可信注册**（推荐理解 ACPs 全流程）和 **快速本地启动**（仅验证 AIP 通信）。

### 2.1 方式一：可信注册（CLI）

完整流程需要 `acps-cli` 及 Registry、CA、Discovery 等后端服务。  
详细 CLI 说明见同级目录 `[../acps-cli/README.md](../acps-cli/README.md)`。

#### 前置条件

- Python 3.10+
- Registry、CA、Discovery 等后端服务已部署并可访问（地址见 `../acps-cli/acps-cli.toml`）



#### 安装 acps-cli

```bash
cd ../acps-cli
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

验证：`acps-cli --help` 能正常输出帮助信息。

#### 准备 ACS 文件

仓库中只提交 `acs.json.example` 模板，本地使用的 `acs.json`（含注册后回写的 AIC）不会进入 git。首次注册前请复制：

```bash
cp ../demo/partner/acps-atr/acs.json.example ../demo/partner/acps-atr/acs.json
cp ../demo/leader/acps-atr/acs.json.example ../demo/leader/acps-atr/acs.json
```



#### Partner 可信注册（示例）

```bash
cd ../acps-cli
source .venv/bin/activate   # 若尚未激活虚拟环境

# 1. 用户登录
acps-cli auth login --username YourUsername --password 'YourPassword'

# 2. 保存 ACS（创建 DRAFT）
acps-cli agent save --acs-file ../demo/partner/acps-atr/acs.json

# 3. 提交审核（→ PENDING）
acps-cli agent submit --agent-id <上一步返回的 agent_id>
acps-cli agent sync --acs-file ../demo/partner/acps-atr/acs.json

# 4. 管理员审核通过（→ APPROVED，分配正式 AIC） 这一步非管理员要等待管理员审核
acps-cli admin auth login --username admin --password 'admin123'
acps-cli admin registry review approve --agent-id <agent_id>

# 同步 ACS（回写正式 AIC 到本地 acs.json）
acps-cli agent sync --acs-file ../demo/partner/acps-atr/acs.json

# 5. 获取 EAB 并申请证书（Partner 为 RPC 服务端，使用 serverAuth）
#    将 AIC 替换为 sync 后 acs.json 中的 aic 字段
AIC=xxx 

acps-cli cert eab fetch --aic "$AIC" --output ../demo/partner/acps-atr/eab.json

mkdir -p ../demo/partner/acps-atr/private ../demo/partner/acps-atr/certs
acps-cli cert issue --aic "$AIC" \
  --eab-file ../demo/partner/acps-atr/eab.json \
  --usage serverAuth \
  c
```

签发完成后，`acps-atr/` 目录应包含：

```text
acps-atr/
  eab.json              # EAB 凭证（申请证书时使用）
  private/{aic}.key
  certs/{aic}.crt
  certs/ca.crt
```

Leader 重复同样流程，使用 `leader/acps-atr/acs.json`；`cert issue` 时改用 `--usage clientAuth`，路径替换为 `../demo/leader/acps-atr/...`。

#### 验证 Discovery 可见性（可选）

```bash
acps-cli discover query \
  --type filtered \
  --filter-json '{"conditions":[{"field":"aic","op":"eq","value":"demo-partner-aic"}]}'
```

返回结果中的 `acsMap` 含 Partner 完整 ACS，包括 `endPoints` 和 `security` 配置。

---



### 2.2 方式二：快速本地启动

若只想快速跑通 **AIP + mTLS + LangChain**，可跳过 Registry 联调，**手动放置证书**后直接启动。

#### Step 1：安装依赖

```bash
cd demo
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```



#### Step 2：准备 ACS 与证书

先从模板复制 `acs.json`（与 `.env` 同理，本地文件不入库）：

```bash
cp partner/acps-atr/acs.json.example partner/acps-atr/acs.json
cp leader/acps-atr/acs.json.example leader/acps-atr/acs.json
```

Leader 与 Partner 各自在 `acps-atr/` 放置证书材料：

```text
acps-atr/
  acs.json
  certs/
    {aic}.crt    # 公钥证书（aic 来自 acs.json）
    ca.crt       # 根证书
  private/
    {aic}.key    # 私钥
```

缺任一文件时 `python main.py` 会报错退出。

#### Step 3：配置 Partner 环境变量

```bash
cp partner/.env.example partner/.env
# 编辑 partner/.env，填入 OPENAI_API_KEY、TAVILY_API_KEY、RPC_PORT 等
```



#### Step 4：启动 Partner

在**第一个终端**：

```bash
cd partner && python main.py
```

Partner 监听 `https://localhost:25002/rpc`（端口由 `RPC_PORT` 控制）。

#### Step 5：启动 Leader

在**第二个终端**：

```bash
cp leader/.env.example leader/.env
# 编辑 leader/.env，配置本地 LLM（如 Ollama）和 RPC_PORT（与 Partner 一致）

cd leader && python main.py
```



#### 示例对话

```text
你: 帮我查一下英伟达目前的股价
[Client] 调用实时信息智能体 (https://localhost:25002/rpc)...
[Server] 收到查询任务: 英伟达目前的股价
[Server] 生成回复: ...
[Client] 远端智能体已回复
助手: 英伟达 (NVDA) 当前股价约 ...
```



#### 停止服务

在各自终端按 `Ctrl+C` 停止 Partner 或 Leader 进程。

---



## 三、如何开发智能体

本 Demo 的 Leader / Partner 源码是入门参考，更系统的开发说明见：

**[DEVELOPMENT.md](./DEVELOPMENT.md)**

该文档包含：

1. **用 LangChain 快速做一个智能体**（`create_agent` + 工具）
2. **Server 侧开发**（`on_start`、Task 数据结构、状态转移）
3. **Client 侧开发**（`@tool` 包装、ADP 发现后再连接）

---



## 相关仓库


| 仓库                           | 说明                                      |
| ---------------------------- | --------------------------------------- |
| `[../acps-sdk](../acps-sdk)` | ACPs Python SDK（AIP / ACS / ADP 模型与客户端） |
| `[../acps-cli](../acps-cli)` | 统一 CLI（Registry / CA / Discovery / 证书）  |


