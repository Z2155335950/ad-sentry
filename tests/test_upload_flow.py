"""端到端：上传 → 占位流水线 → fixture 卡片 → 复核动作落库。"""

import time
from pathlib import Path

from app.main import app
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
IMAGE = ROOT / "fixtures" / "media" / "sample_image.png"


def _wait_done(client: TestClient, job_id: int, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    data: dict = {}
    while time.time() < deadline:
        data = client.get(f"/api/jobs/{job_id}").json()
        if data["job"]["status"] in ("done", "failed"):
            return data
        time.sleep(0.1)
    raise AssertionError(f"job 未在 {timeout}s 内完成: {data.get('job')}")


def test_upload_creates_fixture_card_and_review_flow():
    with TestClient(app) as client:
        response = client.post(
            "/api/upload",
            files={"file": ("sample_image.png", IMAGE.read_bytes(), "image/png")},
        )
        assert response.status_code == 200
        data = _wait_done(client, response.json()["job_id"])

        assert data["job"]["status"] == "done"
        assert data["findings"], "fixture 应产出至少一条信号"
        levels = {finding["level"] for finding in data["findings"]}
        assert "P1" in levels
        assert data["asset"]["media_type"] == "image"
        assert data["asset"]["width"] == 640

        card = data["card"]
        assert "P1" in card["header"]["title"]["content"]
        note = next(el for el in card["elements"] if el["tag"] == "note")
        assert "不构成法律意见" in note["elements"][0]["content"]
        action = next(el for el in card["elements"] if el["tag"] == "action")
        review_url = action["actions"][0]["url"]
        assert "/review/" in review_url

        page = client.get(review_url)
        assert page.status_code == 200
        assert "Nike" in page.text

        finding_id = data["findings"][0]["id"]
        token = review_url.split("token=", 1)[1]

        response = client.post(
            f"/api/review/{finding_id}/action?token={token}",
            json={"action": "reject", "reason": ""},
        )
        assert response.status_code == 400, "驳回必须填写理由"

        response = client.post(
            f"/api/review/{finding_id}/action?token={token}",
            json={"action": "reject", "reason": "误报：自有品牌素材"},
        )
        assert response.status_code == 200
        assert response.json()["review_status"] == "rejected"


def test_upload_rejects_unsupported_file():
    with TestClient(app) as client:
        response = client.post(
            "/api/upload",
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
        assert response.status_code == 415
