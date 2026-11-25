"""飞书事件处理：验签 → 幂等 → 回执 → 下载素材 → 建任务（PROMPT §12）。

幂等键用 message_id（`FeishuEvent.message_id` 唯一索引）；事件重复投递只回执一次。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass

from sqlmodel import Session, select

from app.config import settings
from app.db import engine
from app.feishu import client
from app.models import FeishuEvent
from app.pipeline.ingest import UnsupportedMedia, store_asset

logger = logging.getLogger(__name__)

SUPPORTED_TYPES = {"image", "file", "media"}
RECEIPT_TEXT = "已接收，预计 45 秒内返回风险卡片。"
UNSUPPORTED_TEXT = "目前只支持图片、视频和文件，请重新上传素材。"
DOWNLOAD_FAILED_TEXT = "素材下载失败，请稍后重试，或改用本地上传页。"


@dataclass
class IncomingMessage:
    event_id: str
    message_id: str
    chat_id: str
    message_type: str
    content: dict
    sender: str = ""


def _content_dict(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def parse_event(payload: dict) -> IncomingMessage | None:
    header = payload.get("header") or {}
    event = payload.get("event") or {}
    message = event.get("message") or {}
    message_id = message.get("message_id")
    if not message_id:
        return None
    sender_id = ((event.get("sender") or {}).get("sender_id") or {}).get("open_id") or ""
    return IncomingMessage(
        event_id=header.get("event_id") or "",
        message_id=message_id,
        chat_id=message.get("chat_id") or "",
        message_type=message.get("message_type") or "",
        content=_content_dict(message.get("content")),
        sender=sender_id,
    )


def token_ok(payload: dict) -> bool:
    expected = settings.feishu_verification_token
    if not expected:
        return True
    token = (payload.get("header") or {}).get("token") or payload.get("token")
    return hmac.compare_digest(str(token or ""), expected)


def verify_signature(headers, body: bytes) -> bool:
    """公网回调模式的飞书签名校验；未配置 Encrypt Key 时跳过。"""
    key = settings.feishu_encrypt_key
    if not key:
        return True
    timestamp = headers.get("x-lark-request-timestamp") or ""
    nonce = headers.get("x-lark-request-nonce") or ""
    signature = headers.get("x-lark-signature") or ""
    if not (timestamp and nonce and signature):
        return False
    digest = hashlib.sha256((timestamp + nonce + key).encode("utf-8") + body).hexdigest()
    return hmac.compare_digest(digest, signature)


def decrypt_event(encrypted: str) -> dict:
    from lark_oapi.core.utils import AESCipher

    return json.loads(AESCipher(settings.feishu_encrypt_key).decrypt_str(encrypted))


def _update(record_id: int, **fields) -> None:
    with Session(engine) as session:
        record = session.get(FeishuEvent, record_id)
        if record is None:
            return
        for name, value in fields.items():
            setattr(record, name, value)
        session.add(record)
        session.commit()


def _resource(msg: IncomingMessage) -> tuple[str, str, str]:
    """返回 (file_key, file_type, 文件名)。"""
    if msg.message_type == "image":
        key = msg.content.get("image_key")
        if not key:
            raise ValueError("缺少 image_key")
        return key, "image", f"feishu-{msg.message_id}.png"
    key = msg.content.get("file_key")
    if not key:
        raise ValueError("缺少 file_key")
    if msg.message_type == "media":
        return key, "media", msg.content.get("file_name") or f"feishu-{msg.message_id}.mp4"
    return key, "file", msg.content.get("file_name") or f"feishu-{msg.message_id}.bin"


def handle_event(payload: dict) -> dict:
    msg = parse_event(payload)
    if msg is None:
        return {"ok": True, "ignored": "not_a_message"}

    with Session(engine) as session:
        existing = session.exec(
            select(FeishuEvent).where(FeishuEvent.message_id == msg.message_id)
        ).first()
        if existing is not None:
            return {"ok": True, "duplicate": True, "job_id": existing.job_id}
        record = FeishuEvent(
            event_id=msg.event_id,
            message_id=msg.message_id,
            chat_id=msg.chat_id,
            status="received",
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        record_id = record.id

    if msg.message_type not in SUPPORTED_TYPES:
        client.send_text(msg.chat_id, UNSUPPORTED_TEXT)
        _update(record_id, status="ignored", note=f"unsupported:{msg.message_type}")
        return {"ok": True, "ignored": msg.message_type}

    client.send_text(msg.chat_id, RECEIPT_TEXT)

    try:
        file_key, file_type, filename = _resource(msg)
        data = client.download_resource(msg.message_id, file_key, file_type)
    except Exception as exc:
        logger.warning("飞书素材下载失败 %s: %s", msg.message_id, exc)
        client.send_text(msg.chat_id, DOWNLOAD_FAILED_TEXT)
        _update(record_id, status="failed", note=f"download:{exc}"[:300])
        return {"ok": False, "error": "download_failed"}

    with Session(engine) as session:
        record = session.get(FeishuEvent, record_id)
        try:
            asset, job = store_asset(data, filename, None, session)
        except UnsupportedMedia as exc:
            client.send_text(msg.chat_id, f"暂不支持该素材类型：{exc}")
            record.status = "ignored"
            record.note = str(exc)[:300]
            session.add(record)
            session.commit()
            return {"ok": True, "ignored": "unsupported_media"}

        record.job_id = job.id
        record.status = "queued"
        session.add(record)
        session.commit()
        return {"ok": True, "job_id": job.id, "asset_id": asset.id}
