"""飞书事件：解析、验签、幂等与不支持类型。"""

import hashlib
import json

from app.db import init_db
from app.feishu import events


def _payload(
    message_type: str = "image",
    content: dict | None = None,
    message_id: str = "om_unit_1",
    token: str = "test-token",
) -> dict:
    return {
        "schema": "2.0",
        "header": {
            "event_id": f"evt_{message_id}",
            "event_type": "im.message.receive_v1",
            "token": token,
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_1"}},
            "message": {
                "message_id": message_id,
                "chat_id": "oc_1",
                "message_type": message_type,
                "content": json.dumps(content or {"image_key": "img_1"}),
            },
        },
    }


def test_parse_image_event():
    msg = events.parse_event(_payload())
    assert msg.message_id == "om_unit_1"
    assert msg.chat_id == "oc_1"
    assert msg.message_type == "image"
    assert msg.content["image_key"] == "img_1"
    assert msg.sender == "ou_1"


def test_parse_returns_none_without_message_id():
    assert events.parse_event({"event": {"message": {}}}) is None


def test_token_check():
    assert events.token_ok(_payload())
    assert not events.token_ok(_payload(token="wrong"))


def test_verify_signature_requires_all_headers(monkeypatch):
    monkeypatch.setattr(events.settings, "feishu_encrypt_key", "secret-key")
    body = b'{"a":1}'
    timestamp, nonce = "1700000000", "nonce"
    digest = hashlib.sha256(
        (timestamp + nonce + "secret-key").encode("utf-8") + body
    ).hexdigest()
    headers = {
        "x-lark-request-timestamp": timestamp,
        "x-lark-request-nonce": nonce,
        "x-lark-signature": digest,
    }
    assert events.verify_signature(headers, body)
    assert not events.verify_signature({}, body)


def test_duplicate_message_creates_one_job():
    init_db()
    payload = _payload(message_id="om_dup_unit_1")
    first = events.handle_event(payload)
    second = events.handle_event(payload)

    assert first["job_id"]
    assert second.get("duplicate") is True
    assert second["job_id"] == first["job_id"]


def test_unsupported_message_type_is_ignored():
    init_db()
    result = events.handle_event(
        _payload(message_type="text", content={"text": "hi"}, message_id="om_text_unit_1")
    )
    assert result["ignored"] == "text"
