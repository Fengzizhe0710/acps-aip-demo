# acps-aip-demo

## ACPs 官方项目

本 Demo 依赖的后端服务（Registry、CA、Discovery、MQ 鉴权等）来自 ACPs 官方开源仓库：

**[AIP-PUB/ACPs-community](https://github.com/AIP-PUB/ACPs-community)**

该项目由北京邮电大学牵头、依托中国电子技术标准化研究院开发，是 **ACPs 协议的官方参考实现**，可一键部署完整的 ACPs 服务栈，包括：

| 组件 | 目录 | 作用 |
|------|------|------|
| 协议规范 | `acps-specs/` | ACPs 协议体系定义 |
| 基础设施 | `acps-infra/` | Docker 编排与单机/生产部署 |
| 注册服务 | `registry-server/` | 智能体可信注册（ATR） |
| 证书服务 | `ca-server/` | mTLS 证书签发（EAB 流程） |
| 发现服务 | `discovery-server/` | 智能体发现（ADP） |
| MQ 鉴权 | `mq-auth-server/` | 群组模式 RabbitMQ ACL 与鉴权 |
| CLI / SDK | `acps-cli/`、`acps-sdk/` | 与本仓库同源的工具与开发库 |
| 参考文档 | `acps-docs/` | 平台开发指南、接入教程等 |

与本仓库的关系：

- **ACPs-community** — 部署整套 ACPs 平台（后端 + 官方 demo-leader/demo-partner）
- **acps-aip-demo（本仓库）** — 聚焦 **AIP 2.1 智能体快速开发**，用更精简的 RPC / AMQP 两种 Demo 展示可信注册到运行的完整路径

本地联调时，先用 [ACPs-community](https://github.com/AIP-PUB/ACPs-community) 拉起 Registry / CA / Discovery 等服务，再将 [`acps-cli/acps-cli.toml`](./acps-cli/acps-cli.toml) 中的地址指向对应端口。详细部署见官方仓库 README 与 `acps-docs/` 中的 Getting Started 文档。

## ACPs 是什么

**ACPs**（Agent Collaboration Protocols，智能体协作协议体系）是一套面向多智能体协作的开放协议规范。它定义了智能体如何描述自身能力、完成可信身份注册、获取 mTLS 证书，以及通过标准协议进行安全交互与发现。

核心概念包括：

| 概念 | 说明 |
|------|------|
| **AIC** | Agent Identity Code，智能体唯一身份标识 |
| **ACS** | Agent Capability Specification，智能体能力描述（`acs.json`） |
| **AIP** | Agent Interaction Protocol，智能体之间的任务交互协议 |
| **ATR** | Agent Trust Registry，可信注册与审核服务 |
| **ADP** | Agent Discovery Protocol，已注册智能体的发现与检索 |

## 本仓库做什么

本仓库是 **ACPs 2.1 版本** 的 AIP 智能体开发示例，展示从**可信注册**到**快速开发**的完整路径，包含两种协作模式：

| 模式 | 目录 | 传输方式 | 适用场景 |
|------|------|----------|----------|
| **RPC** | [`demo/`](./demo/) | HTTPS JSON-RPC `/rpc` | Leader 1:1 调用单个 Partner |
| **AMQP（群组）** | [`demo-group/`](./demo-group/) | RabbitMQ + HTTPS `/aip` 邀请 | Leader 经消息队列协调多个 Partner |

两种 Demo **相互独立**，端口已错开，可同时运行。

## 仓库结构

```text
acps-aip-demo/
  README.md           # 本文
  demo/               # RPC 模式 Demo（建议先看）
  demo-group/         # AMQP 群组模式 Demo（需 RabbitMQ）
  acps-cli/           # 可信注册、证书、发现等 CLI 工具
  acps-sdk/           # Python SDK（AIP / ACS / ADP / 群组客户端）
```

## 按需查阅

不必从头到尾通读，按你的目标跳转即可：

| 你想… | 去看 |
|--------|------|
| 理解 AIC / ACS / 注册发证全流程 | [demo/README.md](./demo/README.md) 第一、二节 |
| 用 acps-cli 完成 login → save → submit → sync → cert issue | [demo/README.md](./demo/README.md) §2.1；CLI 细节 [acps-cli/README.md](./acps-cli/README.md) |
| 写 Partner RPC 服务（`on_start`） | [demo/DEVELOPMENT.md](./demo/DEVELOPMENT.md) 第二节 |
| 写 Leader 远程工具（`AipRpcClient`） | [demo/DEVELOPMENT.md](./demo/DEVELOPMENT.md) 第三节 |
| 跑通群组建队、邀请、广播任务 | [demo-group/README.md](./demo-group/README.md) |
| 设计群组 ACS、RPC vs Inbox 邀请 | [demo-group/DEVELOPMENT.md](./demo-group/DEVELOPMENT.md) |
| SDK API 与数据模型 | [acps-sdk/README.md](./acps-sdk/README.md) |
| 部署整套 ACPs 后端服务 | [ACPs-community](https://github.com/AIP-PUB/ACPs-community) 及 `acps-docs/` |

> 若只想快速跑通**点对点调用**，完成 `demo/` 即可；若需要**多智能体广播协作**，再继续 `demo-group/`。

## acps-cli 与 acps-sdk

### acps-cli — 可信注册与运维工具

`acps-cli` 是 ACPs 的统一命令行工具，**不启动任何后端服务**，而是作为客户端对接 Registry、CA、Discovery、MQ 等服务，用于：

- **Registry**：登录、保存/提交 ACS、同步 AIC、审核管理
- **CA**：获取 EAB、申请/续期/吊销 mTLS 证书
- **Discovery**：查询已注册智能体、健康检查
- **MQ**：群组 ACL 管理、Auth API 探测

**使用前请先修改配置文件** [`acps-cli/acps-cli.toml`](./acps-cli/acps-cli.toml)，将 `[registry]`、`[ca]`、`[discovery]`、`[mq]` 中的服务地址改为你实际部署的后端；`[auth]` 指定登录 token 的本地存放路径（由 `acps-cli auth login` 自动写入，**勿提交到 Git**）：

```toml
[registry]
base_url = "http://localhost:9000/registry"
mtls_base_url = "https://localhost:9002"

[auth]
# 普通用户与管理员 token 文件物理分离
user_token_file = "./.acps-cli/tokens/registry-user.json"
admin_token_file = "./.acps-cli/tokens/registry-admin.json"

[ca]
base_url = "http://localhost:9000/ca-server"

[discovery]
base_url = "http://localhost:9000/discovery"

[mq]
group_api_url = "https://localhost:9007"
```

默认路径一般无需修改；执行 `acps-cli auth login` / `acps-cli admin auth login` 后会在 `./.acps-cli/tokens/` 生成对应 JSON。该目录已在 `.gitignore` 中忽略。

也可通过 `--config PATH` 指定其他配置文件。详细命令说明见 [acps-cli/README.md](./acps-cli/README.md)。

### acps-sdk — 智能体开发 SDK

`acps-sdk` 是 ACPs 协议体系的 **Python SDK（2.1）**，供 Demo 和业务代码直接引用：

| 模块 | 用途 |
|------|------|
| `acps_sdk.acs` | 读写、校验 ACS 能力描述 |
| `acps_sdk.aip` | AIP RPC Server / Client（`AipRpcClient` 等） |
| `acps_sdk.adp` | 通过 Discovery 检索智能体 |
| `acps_sdk.aic` | AIC 解析与校验 |

Demo 的 `requirements.txt` 已依赖本 SDK；开发时可 `pip install -e ../acps-sdk` 本地联调。API 文档见 [acps-sdk/README.md](./acps-sdk/README.md)。

## 安装与启动

### 环境要求

- **Python 3.10+**
- **ACPs 后端服务** — 需先部署 [ACPs-community](https://github.com/AIP-PUB/ACPs-community) 中的 Registry / CA / Discovery（及群组模式所需的 MQ 鉴权、RabbitMQ），地址配置在 `acps-cli/acps-cli.toml`
- **demo-group 额外需要 RabbitMQ**（默认 `localhost:5672`，见 [demo-group/README.md](./demo-group/README.md)）

### 1. 安装 acps-cli

```bash
cd acps-cli
python -m venv .venv && source .venv/bin/activate
pip install -e .
acps-cli --help   # 验证安装
```

### 2. 可信注册（可选，推荐首次体验）

`acs.json` 与 `.env` 一样不入库，需先从 `acs.json.example` 复制本地副本：

```bash
# RPC Demo
cp demo/partner/acps-atr/acs.json.example demo/partner/acps-atr/acs.json
cp demo/leader/acps-atr/acs.json.example demo/leader/acps-atr/acs.json

# 群组 Demo（若使用 demo-group）
cp demo-group/partner/acps-atr/acs.json.example demo-group/partner/acps-atr/acs.json
cp demo-group/leader/acps-atr/acs.json.example demo-group/leader/acps-atr/acs.json
```

按 [demo/README.md §2.1](./demo/README.md#21-方式一可信注册cli) 完成 Partner / Leader 的 login → save → submit → sync → cert issue，生成 `acps-atr/` 证书材料后再启动 Demo。

### 3. 启动 RPC Demo

```bash
cd demo
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp partner/acps-atr/acs.json.example partner/acps-atr/acs.json
cp leader/acps-atr/acs.json.example leader/acps-atr/acs.json
cp partner/.env.example partner/.env
cp leader/.env.example leader/.env

# 终端 1 — Partner
cd partner && python main.py

# 终端 2 — Leader
cd leader && python main.py
```

### 4. 启动 AMQP 群组 Demo

见 [demo-group/README.md](./demo-group/README.md) 完整步骤（含 RabbitMQ 启动与群组注册）。

## acps-cli 命令概览

```text
Usage: acps-cli [OPTIONS] COMMAND [ARGS]...

  ACPs unified command line interface.

Options:
  --config TEXT  Path to acps-cli.toml config file.
  --verbose      Enable verbose logging.
  --help         Show this message and exit.

Commands:
  admin     Administrative and control-plane commands.
  agent     Manage Agent drafts and review lifecycle.
  auth      User authentication commands.
  cert      Manage certificate lifecycle operations.
  discover  Run discovery queries and health checks.
  entity    Manage derived entities.
```

各子命令详情：`acps-cli auth --help`、`acps-cli cert --help`、`acps-cli admin --help`。
