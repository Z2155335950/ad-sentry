"""演示回放接口：五个场景可离线打开，结果幂等且符合预置预期。"""

from __future__ import annotations

from app.config import settings
from app.main import app
from fastapi.testclient import TestClient


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "worker_in_app", False)
    return TestClient(app)


def test_demo_index_lists_five_scenarios(monkeypatch):
    with _client(monkeypatch) as client:
        response = client.get("/demo")
        assert response.status_code == 200
        titles = (
            "高风险视频全链路",
            "证据链与授权到期",
            "替换后重检通过",
            "干净 AI 人像素材",
            "历史素材批量巡检",
        )
        for title in titles:
            assert title in response.text


def test_replay_is_idempotent_and_keeps_evidence(monkeypatch):
    with _client(monkeypatch) as client:
        first = client.get("/demo/replay/risk_video?format=json").json()
        second = client.get("/demo/replay/risk_video?format=json").json()

        assert first["job"]["id"] == second["job"]["id"]
        assert [f["id"] for f in first["findings"]] == [f["id"] for f in second["findings"]]
        assert {f["level"] for f in first["findings"]} == {"P0", "P1", "P2"}
        assert "P0" in first["card"]["header"]["title"]["content"]
        assert first["review_urls"]
        assert first["findings"][0]["evidence"]
        review_url = next(iter(first["review_urls"].values()))
        assert client.get(review_url).status_code == 200


def test_license_expiry_raises_bgm_with_license_evidence(monkeypatch):
    with _client(monkeypatch) as client:
        data = client.get("/demo/replay/license_expiry?format=json").json()

        bgm = next(f for f in data["findings"] if f["category"] == "bgm")
        assert bgm["level"] == "P1"
        license_evidence = [ev for ev in bgm["evidence"] if ev["kind"] == "license"]
        assert license_evidence
        assert "过期" in (license_evidence[0].get("note") or "")
        assert data["ledger"]["items"][0]["status"] == "expired"


def test_recheck_pass_and_clean_ai_levels(monkeypatch):
    with _client(monkeypatch) as client:
        passed = client.get("/demo/replay/recheck_pass?format=json").json()
        clean = client.get("/demo/replay/clean_ai?format=json").json()

        assert passed["findings"] == []
        assert passed["card"] is None
        levels = {f["level"] for f in clean["findings"]}
        assert levels == {"P2", "P3"}


def test_batch_scan_totals(monkeypatch):
    with _client(monkeypatch) as client:
        batch = client.get("/demo/replay/batch_scan?format=json").json()["batch"]

        assert batch["assets"] == 4
        assert batch["totals"] == {"P0": 2, "P1": 2, "P2": 2, "P3": 1}
        assert batch["needs_action"] == 4


def test_demo_disabled_returns_404(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "worker_in_app", False)
    with TestClient(app) as client:
        assert client.get("/demo").status_code == 404
        assert client.get("/demo/replay/risk_video").status_code == 404
