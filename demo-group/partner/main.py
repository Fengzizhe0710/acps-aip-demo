"""Partner：群组模式 — POST /aip 接收入群邀请，RabbitMQ 处理广播任务。"""

from __future__ import annotations

import json
import os
import ssl
from pathlib import Path

import uvicorn
from acps_sdk.aip import (
    Product,
    TaskCommand,
    TaskCommandType,
    TaskState,
    TextDataItem,
)
from acps_sdk.aip.aip_group_model import RabbitMQRequest, RabbitMQResponse
from acps_sdk.aip.aip_group_partner import GroupPartnerMqClient
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# 1. 读取配置
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

AIP_PORT = int(os.getenv("AIP_PORT", "25003"))
AIP_URL = f"https://localhost:{AIP_PORT}/aip"

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
RABBITMQ_PORT = int(os.getenv("RABBITMQ_PORT", "5672"))
RABBITMQ_VHOST = os.getenv("RABBITMQ_VHOST", "/")
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "guest")

# ---------------------------------------------------------------------------
# 2. 加载 mTLS 证书
# ---------------------------------------------------------------------------


def _load_mtls(agent_dir: Path) -> tuple[str, Path, Path, Path]:
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

    return aic, cert_file, key_file, ca_file


_partner_aic, _cert_file, _key_file, _ca_file = _load_mtls(ROOT)

# ---------------------------------------------------------------------------
# 3. 群组 MQ 客户端
# ---------------------------------------------------------------------------

_mq_client = GroupPartnerMqClient(
    partner_aic=_partner_aic,
    rabbitmq_host=RABBITMQ_HOST,
    rabbitmq_port=RABBITMQ_PORT,
    rabbitmq_vhost=RABBITMQ_VHOST,
    rabbitmq_user=RABBITMQ_USER,
    rabbitmq_password=RABBITMQ_PASSWORD,
)


def _extract_text(command: TaskCommand) -> str:
    if not command.dataItems:
        return ""
    for item in command.dataItems:
        if isinstance(item, TextDataItem):
            return item.text
    return ""


async def _on_task_command(command: TaskCommand, is_mentioned: bool) -> None:
    if not is_mentioned:
        return
    if command.command != TaskCommandType.Start:
        return

    task_id = command.taskId or command.id
    session_id = command.sessionId or ""
    user_input = _extract_text(command)

    print(f"[Partner] 收到群组任务: {user_input!r}")

    reply = (
        f"我是 Partner（{_partner_aic[-8:]}），"
        f"已收到任务：{user_input or '（无文本）'}。"
        f"当前状态正常，随时协作。"
    )
    print(f"[Partner] 回复: {reply}")

    await _mq_client.send_task_result(
        task_id=task_id,
        session_id=session_id,
        state=TaskState.Completed,
        products=[Product(id=f"prod-{task_id}", dataItems=[TextDataItem(text=reply)])],
    )


_mq_client.set_command_handler(_on_task_command)

# ---------------------------------------------------------------------------
# 4. FastAPI：/aip 接收入群邀请（RabbitMQRequest）
# ---------------------------------------------------------------------------

app = FastAPI()


@app.post("/aip")
async def handle_aip_invite(request: Request) -> JSONResponse:
    body = await request.json()
    rabbitmq_request = RabbitMQRequest.model_validate(body)
    print(f"[Partner] 收到入群邀请 group={rabbitmq_request.params.group.groupId}")

    response: RabbitMQResponse = await _mq_client.join_group(rabbitmq_request)
    payload = response.model_dump(exclude_none=True)
    status_code = 200 if response.error is None else 400
    return JSONResponse(content=payload, status_code=status_code)


# ---------------------------------------------------------------------------
# 5. 启动 HTTPS + mTLS 服务
# ---------------------------------------------------------------------------


def main() -> None:
    print(f"群组 Partner（mTLS）: {AIP_URL}")
    print(f"RabbitMQ: {RABBITMQ_HOST}:{RABBITMQ_PORT}{RABBITMQ_VHOST}")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=AIP_PORT,
        ssl_certfile=str(_cert_file),
        ssl_keyfile=str(_key_file),
        ssl_ca_certs=str(_ca_file),
        ssl_cert_reqs=ssl.CERT_REQUIRED,
    )


if __name__ == "__main__":
    main()
