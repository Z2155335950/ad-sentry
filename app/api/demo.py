"""演示回放接口（PROMPT §16）。仅在 DEMO_MODE=true 时开放。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.api.review import DISCLAIMER, make_review_url
from app.config import settings
from app.db import get_session
from app.demo import aggregate, ensure_scenario, get_scenario, public_scenarios
from app.models import CATEGORY_LABELS
from app.pipeline.report import build_card

BASE_DIR = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter()


def _require_demo() -> None:
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="演示模式未开启（设置 DEMO_MODE=true）")


def _ledger_summary(ledger: dict) -> dict:
    campaign = ledger.get("campaign")
    return {
        "campaign": campaign.model_dump() if campaign else None,
        "campaign_missing": ledger.get("campaign_missing", True),
        "items": [
            {
                "license_id": item["record"].id,
                "scope_type": item["record"].scope_type,
                "start": item["record"].start,
                "end": item["record"].end,
                "status": item["result"].status,
                "status_label": item["status_label"],
                "reasons": item["result"].reasons,
            }
            for item in ledger.get("items") or []
        ],
    }


@router.get("/demo", response_class=HTMLResponse)
def demo_index(request: Request):
    _require_demo()
    return templates.TemplateResponse(
        request,
        "demo.html",
        {"scenarios": public_scenarios(), "disclaimer": DISCLAIMER},
    )


@router.get("/demo/replay/{scenario_id}")
def demo_replay(
    scenario_id: str,
    request: Request,
    fmt: str = Query(default="html", alias="format"),
    session: Session = Depends(get_session),
):
    _require_demo()
    scenario = get_scenario(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="演示场景不存在")

    if scenario.get("kind") == "batch":
        data = aggregate(session)
        if fmt == "json":
            return {"scenario": scenario, "batch": data}
        return templates.TemplateResponse(
            request,
            "demo_batch.html",
            {
                "scenario": scenario,
                "batch": data,
                "disclaimer": DISCLAIMER,
            },
        )

    seeded = ensure_scenario(session, scenario)
    findings = seeded["findings"]
    for finding in findings:
        finding["category_label"] = CATEGORY_LABELS.get(
            finding["category"], finding["category"]
        )
    review_urls = {item["id"]: make_review_url(request, item["id"]) for item in findings}
    card = build_card(findings, review_urls) if findings else None
    ledger = _ledger_summary(seeded["ledger"])

    if fmt == "json":
        return {
            "scenario": {
                "id": scenario["id"],
                "title": scenario["title"],
                "summary": scenario.get("summary", ""),
                "expected": scenario.get("expected", ""),
            },
            "asset": seeded["asset"].model_dump(),
            "job": seeded["job"].model_dump(),
            "findings": findings,
            "card": card,
            "review_urls": review_urls,
            "ledger": ledger,
        }

    return templates.TemplateResponse(
        request,
        "demo_result.html",
        {
            "scenario": scenario,
            "asset": seeded["asset"],
            "job": seeded["job"],
            "findings": findings,
            "card": card,
            "review_urls": review_urls,
            "ledger": ledger,
            "disclaimer": DISCLAIMER,
        },
    )
