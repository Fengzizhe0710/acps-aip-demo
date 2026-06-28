# ACPs AIP 群组模式 Demo

本目录 `demo-group/` 演示 **AIP 群组模式（Group Mode）**：Leader 通过 **RabbitMQ** 创建群组、邀请 Partner 入群，并向群内 **广播任务**；Partner 通过 **HTTPS `/aip`** 接受入群邀请，再在 MQ 上处理任务。

```text
Leader                          Partner
  │                               │
  │  POST /aip (RabbitMQRequest)  │
  │ ─────────────────────────────►│  join_group()
  │                               │
  │◄──── RabbitMQ fanout ────────►│  广播 TaskCommand / TaskResult
  │     (群组 Exchange)            │
```

与同级 [`../demo/`](../demo/) 的 **1:1 RPC 模式** 互补：RPC Demo 展示点对点调用；本 Demo 展示 **1:N 协作**。

## 目录结构

```text
demo-group/
  README.md              # 本文：概念 + 启动演示
  DEVELOPMENT.md         # 群组模式开发策略
  requirements.txt
  leader/
    main.py              # GroupLeaderMqClient：建群、邀请、广播
    acps-atr/            # Leader 注册材料（含 AMQP inbox）
  partner/
    main.py              # POST /aip + GroupPartnerMqClient
    acps-atr/            # Partner 注册材料（AMQP + JSONRPC /aip）
```

## 前置条件

除 ACPs Registry / CA 外，本 Demo **额外需要 RabbitMQ**：

| 组件 | 默认地址 | 说明 |
|------|---------|------|
| RabbitMQ | `localhost:5672` | 默认账号 `guest` / `guest` |
| Partner `/aip` | `https://localhost:25003/aip` | 与 RPC Demo 的 `25002` 错开 |
| mTLS 证书 | 各角色 `acps-atr/` | 流程同 demo |

> 若尚未熟悉 ACPs 注册与证书流程，请先阅读 [`../demo/README.md`](../demo/README.md)。

### 启动 RabbitMQ

本 Demo 连接 **`localhost:5672`**（`.env` 中 `RABBITMQ_HOST` / `RABBITMQ_PORT`）。若本机尚未运行 RabbitMQ，可用 Docker 快速启动：

```bash
docker run -d --name acps-rabbitmq \
  -p 5672:5672 \
  -p 15672:15672 \
  -e RABBITMQ_DEFAULT_USER=guest \
  -e RABBITMQ_DEFAULT_PASS=guest \
  rabbitmq:4-management
```

Management 控制台：<http://localhost:15672>（账号 `guest` / `guest`）。

验证 MQ 已就绪：

```bash
nc -zv localhost 5672
```

停止并删除容器：

```bash
docker stop acps-rabbitmq && docker rm acps-rabbitmq
```

---

## 一、ACPs 群组模式要点

| 概念 | 说明 |
|------|------|
| **群组 Exchange** | Leader 创建的 RabbitMQ fanout exchange，成员队列绑定后接收广播 |
| **入群邀请** | Leader 向 Partner 的 `/aip` 发送 `RabbitMQRequest`（method 固定为群组协议） |
| **mentions** | 广播任务时可指定 `"all"` 或 Partner AIC 列表，未 @ 的 Partner 可忽略 |
| **messageQueue** | ACS `capabilities.messageQueue` 需声明如 `rabbitmq:>=4.2` |
| **双端点 Partner** | AMQP（inbox）+ JSONRPC（`/aip`）；本 Demo 使用 RPC 邀请路径 |

### ACS 与 demo 的差异

| 角色 | demo (RPC) | demo-group |
|------|-----------|------------|
| Partner 端点 | `https://.../rpc` | `amqp://...?inbox=...` + `https://.../aip` |
| Leader 端点 | 无（纯 Client） | `amqp://...?inbox=...` |
| SDK 客户端 | `AipRpcClient` / RPC Server | `GroupLeaderMqClient` / `GroupPartnerMqClient` |

---

## 二、启动项目演示

### 2.1 方式一：可信注册（CLI）

流程与 [`../demo/README.md`](../demo/README.md) 相同，仅路径改为 `demo-group`。

#### 准备 ACS 文件

```bash
cp ../demo-group/partner/acps-atr/acs.json.example ../demo-group/partner/acps-atr/acs.json
cp ../demo-group/leader/acps-atr/acs.json.example ../demo-group/leader/acps-atr/acs.json
```

#### Partner 注册

```bash
cd ../acps-cli
source .venv/bin/activate

acps-cli auth login --username YourUsername --password 'YourPassword'
acps-cli agent save --acs-file ../demo-group/partner/acps-atr/acs.json
acps-cli agent submit --agent-id <agent_id>
acps-cli agent sync --acs-file ../demo-group/partner/acps-atr/acs.json

# 管理员审核
acps-cli admin auth login --username AdminUsername --password 'AdminPassword'
acps-cli admin registry review approve --agent-id <agent_id>

# 同步ACS
acps-cli agent sync --acs-file ../demo-group/partner/acps-atr/acs.json

AIC=$(python -c "import json; print(json.load(open('../demo-group/partner/acps-atr/acs.json'))['aic'])")

acps-cli cert eab fetch --aic "$AIC" --output ../demo-group/partner/acps-atr/eab.json
mkdir -p ../demo-group/partner/acps-atr/private ../demo-group/partner/acps-atr/certs
acps-cli cert issue --aic "$AIC" \
  --eab-file ../demo-group/partner/acps-atr/eab.json \
  --usage serverAuth \
  --key-path "../demo-group/partner/acps-atr/private/${AIC}.key" \
  --cert-path "../demo-group/partner/acps-atr/certs/${AIC}.crt" \
  --trust-bundle-path ../demo-group/partner/acps-atr/certs/ca.crt
```

#### Leader 注册

重复上述步骤，替换为 `../demo-group/leader/acps-atr/...`，`cert issue` 使用 `--usage clientAuth`。

---

### 2.2 方式二：快速本地启动

#### Step 0：确认 RabbitMQ 已启动

按上文 [启动 RabbitMQ](#启动-rabbitmq) 在本机拉起 MQ，并确认 `localhost:5672` 可访问。

#### Step 1：安装依赖

```bash
cd demo-group
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

可与 `demo/.venv` 共用，也可单独创建。

#### Step 2：准备 ACS 与证书

先从模板复制 `acs.json`：

```bash
cp partner/acps-atr/acs.json.example partner/acps-atr/acs.json
cp leader/acps-atr/acs.json.example leader/acps-atr/acs.json
```

Leader 与 Partner 各自在 `acps-atr/` 放置 `acs.json`（含正式 `aic`）、`certs/{aic}.crt`、`certs/ca.crt`、`private/{aic}.key`。

#### Step 3：配置环境变量

```bash
cp partner/.env.example partner/.env
cp leader/.env.example leader/.env
# leader/.env 中 PARTNER_ACS_FILE 默认指向 ../partner/acps-atr/acs.json
```

#### Step 4：启动 Partner

**终端 1**：

```bash
cd partner && python main.py
```

应看到：

```text
群组 Partner（mTLS）: https://localhost:25003/aip
```

#### Step 5：运行 Leader 演示

**终端 2**：

```bash
cd leader && python main.py
```

Leader 将自动：连接 MQ → 建群 → 邀请 Partner → 广播任务 → 等待回复 → 解散群组。

#### 预期输出（节选）

```text
[Leader] 群组已创建: group-xxxxxxxx
[Leader] 邀请 Partner xxxxxxxx 入群...
[Partner] 收到入群邀请 group=group-xxxxxxxx
[Leader] Partner 已入群 queue=...
[Leader] 已广播任务: '请各位 Partner 自我介绍并报告状态！'
[Partner] 收到群组任务: '请各位 Partner 自我介绍并报告状态！'
[Leader] 收到 TaskResult sender=xxxxxxxx state=Completed text='我是 Partner...'
[Leader] 演示完成，收到 Partner 回复。
```

---

## 三、如何开发群组智能体

详见 **[DEVELOPMENT.md](./DEVELOPMENT.md)**：ACS 设计、Leader/Partner 职责、邀请路径（RPC vs Inbox）、任务状态机与扩展建议。

---

## 相关文档

| 文档 | 说明 |
|------|------|
| [`../README.md`](../README.md) | 仓库总览与阅读顺序 |
| [`../demo/README.md`](../demo/README.md) | RPC 模式 Demo（建议先看） |
| [`../acps-sdk/acps_sdk/aip/README.md`](../acps-sdk/acps_sdk/aip/README.md) | SDK AIP 模式说明 |
