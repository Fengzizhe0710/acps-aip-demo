"""Partner：LangChain Agent + AIP RPC 服务（mTLS）。"""

from __future__ import annotations

import json
import os
import ssl
from datetime import datetime
from pathlib import Path

import uvicorn
from acps_sdk.aip import Product, TaskCommand, TaskResult, TaskState, TextDataItem
from acps_sdk.aip.aip_rpc_server import (
    CommandHandlers,
    TaskManager,
    add_aip_rpc_router,
)
from dotenv import load_dotenv
from fastapi import FastAPI
from langchain.agents import create_agent
from langchain_tavily import TavilySearch
from langchain_openai import ChatOpenAI

# ---------------------------------------------------------------------------
# 1. 读取配置
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

RPC_PORT = int(os.getenv("RPC_PORT", "25002"))
RPC_URL = f"https://localhost:{RPC_PORT}/rpc"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# ---------------------------------------------------------------------------
# 2. 加载 mTLS 证书
#    - acps-atr/certs/   公钥证书：{aic}.crt、ca.crt
#    - acps-atr/private/ 私钥：{aic}.key
# ---------------------------------------------------------------------------


def _load_mtls(agent_dir: Path) -> tuple[Path, Path, Path]:
    atr = agent_dir / "acps-atr"
    with (atr / "acs.json").open(encoding="utf-8") as file:
        aic = (json.load(file).get("aic") or "").strip()
    if not aic:
        raise FileNotFoundError("acs.json 缺少 aic 字段")

    cert_file = atr / "certs" / f"{aic}.crt"
    key_file = atr / "private" / f"{aic}.key"
    ca_file = atr / "certs" / "ca.crt"

    for path in (cert_file, key_file, ca_file):
        if not path.is_file():
            raise FileNotFoundError(f"缺少证书: {path}")

    return cert_file, key_file, ca_file


# ---------------------------------------------------------------------------
# 3. 定义任务处理（Leader 通过 RPC 调用 on_start）
# ---------------------------------------------------------------------------


async def on_start(command: TaskCommand, task: TaskResult | None) -> TaskResult:
    user_input = ""
    if command.dataItems and len(command.dataItems) > 0:
        if isinstance(command.dataItems[0], TextDataItem):
            user_input = command.dataItems[0].text

    print(f"[Server] 收到查询任务: {user_input}")

    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    system_prompt = f"""你是一个实时信息智能体。
当前服务器时间是: {current_time}。
请使用搜索工具来准确解答用户的问题。
严禁输出任何形式的推理过程或思考标签，只需直接给出清晰的答案。"""

    llm = ChatOpenAI(
        model=OPENAI_MODEL,
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
        temperature=0.0,
    )
    tools = [TavilySearch(max_results=2, tavily_api_key=TAVILY_API_KEY)]
    agent = create_agent(model=llm, tools=tools, system_prompt=system_prompt)

    result = await agent.ainvoke({"messages": [{"role": "user", "content": user_input}]})
    answer = result["messages"][-1].content
    print(f"[Server] 生成回复: {answer}")

    new_task = TaskManager.create_task(command, initial_state=TaskState.Completed)
    product = Product(id=f"prod-{command.taskId}", dataItems=[TextDataItem(text=answer)])
    new_task.products = [product]
    return new_task


# ---------------------------------------------------------------------------
# 4. 注册 AIP RPC 服务
# ---------------------------------------------------------------------------

handlers = CommandHandlers(on_start=on_start)
app = FastAPI()
add_aip_rpc_router(app, "/rpc", handlers)


# ---------------------------------------------------------------------------
# 5. 启动服务器（HTTPS + mTLS）
# ---------------------------------------------------------------------------


def main() -> None:
    cert_file, key_file, ca_file = _load_mtls(ROOT)
    print(f"RPC 地址（mTLS）: {RPC_URL}")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=RPC_PORT,
        ssl_certfile=str(cert_file),
        ssl_keyfile=str(key_file),
        ssl_ca_certs=str(ca_file),
        ssl_cert_reqs=ssl.CERT_REQUIRED,
    )


if __name__ == "__main__":
    main()
