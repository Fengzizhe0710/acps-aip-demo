"""Leader：LangChain Agent + AIP RPC（mTLS）调用 Partner。"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import uuid
from pathlib import Path

from acps_sdk.aip import AipRpcClient, TaskState
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI

# ---------------------------------------------------------------------------
# 1. 读取配置
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

RPC_PORT = int(os.getenv("RPC_PORT", "25002"))
PARTNER_RPC_URL = f"https://localhost:{RPC_PORT}/rpc"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "local")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "qwen2.5:latest")

SYSTEM_PROMPT = (
    "你是一个得力的助手。你必须直接输出最终答案或工具调用。\n"
    "你可以使用远程实时信息智能体工具来回答用户的请求。"
)

# ---------------------------------------------------------------------------
# 2. 加载 mTLS 证书
#    - acps-atr/certs/   公钥证书：{aic}.crt、ca.crt
#    - acps-atr/private/ 私钥：{aic}.key
# ---------------------------------------------------------------------------


def _load_mtls(agent_dir: Path) -> tuple[str, ssl.SSLContext]:
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

    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ctx.load_cert_chain(str(cert_file), str(key_file))
    ctx.load_verify_locations(cafile=str(ca_file))
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return aic, ctx


_leader_aic, _ssl_context = _load_mtls(ROOT)

# ---------------------------------------------------------------------------
# 3. 定义 AIP 工具（LangChain 会在需要时自动调用）
# ---------------------------------------------------------------------------


@tool
async def query_realtime_agent(query: str) -> str:
    """调用远程实时信息智能体，获取最新的股价、天气、新闻或专家知识解答。"""
    print(f"[Client] 调用实时信息智能体 ({PARTNER_RPC_URL})...")
    client = AipRpcClient(
        partner_url=PARTNER_RPC_URL,
        leader_id=_leader_aic,
        ssl_context=_ssl_context,
    )
    session_id = f"session-{uuid.uuid4()}"
    task_id = f"task-{uuid.uuid4()}"

    try:
        task = await client.start_task(
            session_id=session_id,
            task_id=task_id,
            user_input=query,
        )
        while task.status.state in (TaskState.Accepted, TaskState.Working):
            await asyncio.sleep(0.2)
            task = await client.get_task(task_id=task_id, session_id=session_id)

        if task.status.state == TaskState.AwaitingCompletion:
            await client.complete_task(task_id=task_id, session_id=session_id)

        if task.status.state in (TaskState.Completed, TaskState.AwaitingCompletion):
            answer = task.products[0].dataItems[0].text
            print("[Client] 远端智能体已回复")
            return answer

        return f"Partner 任务未完成：{task.status.state}"
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# 4. 创建 LangChain Agent（本地 LLM + 上面的 AIP 工具）
# ---------------------------------------------------------------------------

llm = ChatOpenAI(
    model=OPENAI_MODEL,
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL,
    timeout=180.0,
    temperature=0.0,
)
tools = [query_realtime_agent]
agent = create_agent(model=llm, tools=tools, system_prompt=SYSTEM_PROMPT)


# ---------------------------------------------------------------------------
# 5. 多轮对话主循环
# ---------------------------------------------------------------------------


async def main() -> None:
    print(f"Demo Leader（mTLS，Partner: {PARTNER_RPC_URL}）")
    print("输入 quit 退出。\n")

    messages: list[dict] = []

    while True:
        user_input = input("你: ").strip()
        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit", "q"}:
            break

        messages.append({"role": "user", "content": user_input})
        try:
            result = await agent.ainvoke({"messages": messages})
            reply = result["messages"][-1].content
            print(f"助手: {reply}\n")
            messages = result["messages"]
        except Exception as exc:
            print(f"错误: {exc}\n")
            messages.pop()


if __name__ == "__main__":
    asyncio.run(main())
