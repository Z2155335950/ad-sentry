"""HMAC 签名 URL 与复核动作的鉴权。"""

from app.api.review import make_token, verify_token
from app.main import app
from fastapi.testclient import TestClient


def test_token_roundtrip():
    token = make_token(7)
    assert verify_token(7, token)
    assert not verify_token(8, token)
    assert not verify_token(7, "garbage")
    assert not verify_token(7, "")
    assert not verify_token(7, make_token(7, ttl_hours=-1))


def test_action_rejects_bad_token():
    with TestClient(app) as client:
        response = client.post(
            "/api/review/1/action?token=bogus", json={"action": "confirm"}
        )
        assert response.status_code in (403, 404)


def test_review_page_requires_valid_token():
    with TestClient(app) as client:
        response = client.get("/review/1?token=garbage")
        assert response.status_code in (403, 404)
