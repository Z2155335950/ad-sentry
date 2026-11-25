"""飞书开放平台调用（PROMPT §12）。

所有 HTTP 调用集中在这里：tenant access token、发消息、下载资源、多维表格。
业务代码不拼飞书 URL，也不直接 import 供应商 SDK（长连接在 `longconn.py` 单独隔离）。
`FEISHU_MODE=mock` 时写 `DATA_DIR/feishu_outbox.jsonl`，便于离线验收。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_token_cache: dict[str, tuple[str, float]] = {}


class FeishuError(RuntimeError):
    pass


def is_mock() -> bool:
    return (settings.feishu_mode or "live").lower() == "mock"


def _api(path: str) -> str:
    return f"{settings.feishu_domain.rstrip('/')}{path}"


def _outbox_path() -> Path:
    return settings.data_dir / "feishu_outbox.jsonl"


def _write_outbox(entry: dict) -> None:
    path = _outbox_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_outbox() -> list[dict]:
    path = _outbox_path()
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


def _check(payload: dict, what: str) -> dict:
    code = payload.get("code")
    if code not in (0, None):
        raise FeishuError(f"{what} 失败 code={code} msg={payload.get('msg')}")
    return payload


def tenant_token() -> str:
    if is_mock():
        return "mock-tenant-token"
    cached = _token_cache.get(settings.feishu_app_id)
    if cached and cached[1] > time.time():
        return cached[0]
    if not (settings.feishu_app_id and settings.feishu_app_secret):
        raise FeishuError("未配置 FEISHU_APP_ID / FEISHU_APP_SECRET")

    response = httpx.post(
        _api("/open-apis/auth/v3/tenant_access_token/internal"),
        json={
            "app_id": settings.feishu_app_id,
            "app_secret": settings.feishu_app_secret,
        },
        timeout=10.0,
    )
    response.raise_for_status()
    payload = _check(response.json(), "获取 tenant_access_token")
    token = payload.get("tenant_access_token") or ""
    if not token:
        raise FeishuError("tenant_access_token 为空")
    # 提前 60 秒过期，避免边界请求失败
    expires_at = time.time() + int(payload.get("expire", 7200)) - 60
    _token_cache[settings.feishu_app_id] = (token, expires_at)
    return token


def send_message(chat_id: str, msg_type: str, content: dict) -> dict:
    if is_mock():
        _write_outbox(
            {
                "kind": "message",
                "chat_id": chat_id,
                "msg_type": msg_type,
                "content": content,
                "ts": time.time(),
            }
        )
        return {"mock": True, "chat_id": chat_id, "msg_type": msg_type}

    response = httpx.post(
        _api("/open-apis/im/v1/messages"),
        params={"receive_id_type": "chat_id"},
        headers={"Authorization": f"Bearer {tenant_token()}"},
        json={
            "receive_id": chat_id,
            "msg_type": msg_type,
            "content": json.dumps(content, ensure_ascii=False),
        },
        timeout=10.0,
    )
    response.raise_for_status()
    return _check(response.json(), "发送消息")


def send_text(chat_id: str, text: str) -> dict:
    return send_message(chat_id, "text", {"text": text})


def send_card(chat_id: str, card: dict) -> dict:
    return send_message(chat_id, "interactive", card)


def download_resource(message_id: str, file_key: str, file_type: str) -> bytes:
    """file_type: image | file | media（按飞书消息类型传）。"""
    if is_mock():
        fallback = settings.fixtures_dir / "media" / "sample_image.png"
        return fallback.read_bytes()

    response = httpx.get(
        _api(f"/open-apis/im/v1/messages/{message_id}/resources/{file_key}"),
        params={"type": file_type},
        headers={"Authorization": f"Bearer {tenant_token()}"},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.content


def append_bitable(fields: dict) -> dict | None:
    """多维表格留档（P1）。未配置 app_token / table_id 时静默跳过。"""
    app_token = settings.feishu_bitable_app_token
    table_id = settings.feishu_bitable_table_id
    if not (app_token and table_id):
        return None
    if is_mock():
        _write_outbox({"kind": "bitable", "fields": fields, "ts": time.time()})
        return {"mock": True}

    response = httpx.post(
        _api(f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"),
        headers={"Authorization": f"Bearer {tenant_token()}"},
        json={"fields": fields},
        timeout=10.0,
    )
    response.raise_for_status()
    return _check(response.json(), "多维表格写入")
