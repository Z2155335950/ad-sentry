"""授权台账 API：上传抽取 → 登记投放 → 挂接素材 → 状态重算 → 人工补录。"""

from pathlib import Path

import pytest
from app.main import app
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
IMAGE = ROOT / "fixtures" / "media" / "sample_image.png"


def _pdf_bytes(lines: list[str]) -> bytes:
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    page = doc.new_page()
    y = 80
    for line in lines:
        page.insert_text((64, y), line, fontsize=10)
        y += 18
    data = doc.tobytes()
    doc.close()
    return data


def _licence_lines(territory: str, end: str) -> list[str]:
    return [
        "Licensor: Star Music Publishing Ltd.",
        "Licensee: Flyer AdCo",
        "Scope: music",
        "Scope Items: Neon Sky",
        f"Territory: {territory}",
        "Media: TikTok, Meta",
        "Start: 2026-01-01",
        f"End: {end}",
    ]


def test_upload_campaign_link_and_status():
    with TestClient(app) as client:
        uploaded = client.post(
            "/api/licenses/upload",
            files={
                "file": (
                    "licence.pdf",
                    _pdf_bytes(_licence_lines("US, EU", "2027-12-31")),
                    "application/pdf",
                )
            },
        )
        assert uploaded.status_code == 200, uploaded.text
        payload = uploaded.json()
        record = payload["record"]
        assert payload["text_source"] == "pdf"
        assert payload["missing_fields"] == []
        assert record["territory"] == ["US", "EU"]
        assert record["status"] == "effective"

        media = client.post(
            "/api/upload",
            files={"file": ("sample_image.png", IMAGE.read_bytes(), "image/png")},
        )
        asset_id = media.json()["asset_id"]

        campaign = client.post(
            f"/api/assets/{asset_id}/campaign",
            json={"platform": "tiktok", "market": "SEA", "start": "2026-12-01"},
        )
        assert campaign.status_code == 200, campaign.text

        linked = client.post(
            f"/api/licenses/{record['id']}/link", json={"asset_id": asset_id}
        )
        assert linked.status_code == 200, linked.text

        ledger = client.get(f"/api/assets/{asset_id}/ledger").json()
        assert ledger["campaign_missing"] is False
        statuses = {item["record"]["id"]: item["status"] for item in ledger["licenses"]}
        assert statuses[record["id"]] == "out_of_territory"
        assert any("地域" in reason for reason in ledger["licenses"][0]["reasons"])


def test_missing_fields_go_to_needs_review_and_patch_fixes_them():
    with TestClient(app) as client:
        uploaded = client.post(
            "/api/licenses/upload",
            files={
                "file": (
                    "incomplete.pdf",
                    _pdf_bytes(["Licensor: Some Studio", "Territory: US"]),
                    "application/pdf",
                )
            },
        )
        assert uploaded.status_code == 200, uploaded.text
        payload = uploaded.json()
        assert set(payload["missing_fields"]) == {"scope_type", "start", "end"}
        record_id = payload["record"]["id"]
        assert payload["record"]["status"] == "needs_review"

        patched = client.patch(
            f"/api/licenses/{record_id}",
            json={
                "scope_type": "music",
                "start": "2026-01-01",
                "end": "2027-12-31",
            },
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["status"] == "effective"


def test_rejects_unsupported_licence_format():
    with TestClient(app) as client:
        response = client.post(
            "/api/licenses/upload",
            files={"file": ("licence.txt", b"plain text", "text/plain")},
        )
        assert response.status_code == 415
