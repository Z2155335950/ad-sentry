"""飞书回调入口：url_verification、Token 校验、事件建任务与幂等。"""

import json

from app.main import app
from fastapi.testclient import TestClient


def _event(message_id: str = "om_api_1", token: str = "test-token") -> dict:
    return {
        "schema": "2.0",
        "header": {
            "event_id": f"evt_{message_id}",
            "event_type": "im.message.receive_v1",
            "token": token,
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_api"}},
            "message": {
                "message_id": message_id,
                "chat_id": "oc_api",
                "message_type": "image",
                "content": json.dumps({"image_key": "img_api"}),
            },
        },
    }


def test_url_verification_echoes_challenge():
    with TestClient(app) as client:
        response = client.post(
            "/api/feishu/event",
            json={"type": "url_verification", "token": "test-token", "challenge": "c1"},
        )
        assert response.status_code == 200
        assert response.json()["challenge"] == "c1"


def test_event_creates_job_and_dedupes():
    event = _event("om_api_2")
    with TestClient(app) as client:
        first = client.post("/api/feishu/event", json=event)
        assert first.status_code == 200, first.text
        assert first.json()["job_id"]

        second = client.post("/api/feishu/event", json=event)
        assert second.json()["duplicate"] is True


def test_token_mismatch_is_rejected():
    with TestClient(app) as client:
        response = client.post("/api/feishu/event", json=_event("om_api_3", token="wrong"))
        assert response.status_code == 403
