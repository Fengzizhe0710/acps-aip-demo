"""Leader：群组模式 — 创建群组、邀请 Partner、广播任务。"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import uuid
from pathlib import Path

from acps_sdk.aip import TaskResult, TaskState
from acps_sdk.aip.aip_group_leader import GroupLeaderMqClient
from acps_sdk.aip.aip_group_model import ACSObject
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# 1. 读取配置
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

PARTNER_AIP_URL = os.getenv("PARTNER_AIP_URL", "https://localhost:25003/aip")
PARTNER_ACS_FILE = os.getenv(
    "PARTNER_ACS_FILE", str(ROOT.parent / "partner" / "acps-atr" / "acs.json")
)
GROUP_TASK_TEXT = os.getenv(
    "GROUP_TASK_TEXT", "请各位 Partner 自我介绍并报告状态！"
)
WAIT_SECONDS = int(os.getenv("WAIT_SECONDS", "30"))

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
RABBITMQ_PORT = int(os.getenv("RABBITMQ_PORT", "5672"))
RABBITMQ_VHOST = os.getenv("RABBITMQ_VHOST", "/")
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "guest")

# ---------------------------------------------------------------------------
# 2. 加载 mTLS 证书
# ---------------------------------------------------------------------------


def _load_mtls(agent_dir: Path) -> tuple[str, ssl.SSLContext, tuple[str, str]]:
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
    client_cert = (str(cert_file), str(key_file))
    return aic, ctx, client_cert


def _load_partner_aic(acs_path: Path) -> str:
    with acs_path.open(encoding="utf-8") as file:
        aic = (json.load(file).get("aic") or "").strip()
    if not aic:
        raise FileNotFoundError(f"{acs_path} 缺少 aic 字段")
    return aic


_leader_aic, _ssl_context, _client_cert = _load_mtls(ROOT)
_partner_aic = _load_partner_aic(Path(PARTNER_ACS_FILE))

# ---------------------------------------------------------------------------
# 3. 群组演示主流程
# ---------------------------------------------------------------------------

_received_results: list[TaskResult] = []


async def _on_group_message(message: object) -> None:
    if isinstance(message, TaskResult):
        state = message.status.state if message.status else None
        sender = message.senderId or "unknown"
        text = ""
        if message.status and message.status.dataItems:
            for item in message.status.dataItems:
                if hasattr(item, "text"):
                    text = item.text
                    break
        if not text and message.products:
            for product in message.products:
                for item in product.dataItems or []:
                    if hasattr(item, "text"):
                        text = item.text
                        break
        print(f"[Leader] 收到 TaskResult sender={sender[-8:]} state={state} text={text!r}")
        if state in (TaskState.Completed, TaskState.Failed, TaskState.Canceled):
            _received_results.append(message)


async def main() -> None:
    print(f"Demo Group Leader（AIC: {_leader_aic}）")
    print(f"Partner AIP: {PARTNER_AIP_URL}")
    print(f"RabbitMQ: {RABBITMQ_HOST}:{RABBITMQ_PORT}{RABBITMQ_VHOST}\n")

    client = GroupLeaderMqClient(
        leader_aic=_leader_aic,
        rabbitmq_host=RABBITMQ_HOST,
        rabbitmq_port=RABBITMQ_PORT,
        rabbitmq_vhost=RABBITMQ_VHOST,
        rabbitmq_user=RABBITMQ_USER,
        rabbitmq_password=RABBITMQ_PASSWORD,
    )

    try:
        await client.connect()
        group_id = await client.create_group(group_id=f"group-{uuid.uuid4().hex[:8]}")
        print(f"[Leader] 群组已创建: {group_id}")

        client.set_message_handler(_on_group_message)
        await client.start_consuming()

        partner_acs = ACSObject(aic=_partner_aic)
        print(f"[Leader] 邀请 Partner {_partner_aic[-8:]} 入群...")
        partner_info = await client.invite_partner(
            partner_acs=partner_acs,
            partner_rpc_url=PARTNER_AIP_URL,
            ssl_context=_ssl_context,
            client_cert=_client_cert,
        )
        print(f"[Leader] Partner 已入群 queue={partner_info.queue_name}")

        session_id = f"session-{uuid.uuid4().hex[:8]}"
        task_id = await client.start_task(
            session_id=session_id,
            text_content=GROUP_TASK_TEXT,
            mentions="all",
        )
        print(f"[Leader] 已广播任务 task_id={task_id}: {GROUP_TASK_TEXT!r}")
        print(f"[Leader] 等待 Partner 回复（最多 {WAIT_SECONDS}s）...\n")

        for _ in range(WAIT_SECONDS * 2):
            if _received_results:
                break
            await asyncio.sleep(0.5)

        if _received_results:
            print("\n[Leader] 演示完成，收到 Partner 回复。")
        else:
            print("\n[Leader] 超时：未收到 Partner TaskResult，请检查 Partner 与 RabbitMQ。")

        await client.dissolve_group()
        print("[Leader] 群组已解散。")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
