"""复核页与复核动作。鉴权用 HMAC 签名 URL，不引入用户体系。"""

from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlmodel import Session, select

from app.config import settings
from app.db import get_session
from app.models import (
    CATEGORY_LABELS,
    Asset,
    Evidence,
    Finding,
    ReviewAction,
    ReviewActionType,
    ReviewStatus,
)

BASE_DIR = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter()

DISCLAIMER = (
    "本工具输出为基于公开信息的风险提示与技术证据，不构成法律意见；"
    "上线前请结合授权文件与法务意见自行判断。"
)


def _sign(payload: str) -> str:
    return hmac.new(
        settings.app_hmac_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def make_token(finding_id: int, ttl_hours: float | None = None) -> str:
    ttl = settings.review_token_ttl_hours if ttl_hours is None else ttl_hours
    expires_at = int(time.time() + ttl * 3600)
    return f"{expires_at}.{_sign(f'{finding_id}:{expires_at}')}"


def verify_token(finding_id: int, token: str) -> bool:
    try:
        expires_raw, signature = token.split(".", 1)
        expires_at = int(expires_raw)
    except (AttributeError, ValueError):
        return False
    if expires_at < time.time():
        return False
    return hmac.compare_digest(_sign(f"{finding_id}:{expires_at}"), signature)


def make_review_url(request: Request, finding_id: int) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/review/{finding_id}?token={make_token(finding_id)}"


def _get_finding_or_403(session: Session, finding_id: int, token: str) -> Finding:
    finding = session.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="信号不存在")
    if not verify_token(finding_id, token):
        raise HTTPException(status_code=403, detail="签名无效或已过期")
    return finding


REVIEW_STATUS_LABELS = {
    ReviewStatus.pending.value: "待复核",
    ReviewStatus.confirmed.value: "已确认",
    ReviewStatus.rejected.value: "已驳回",
}


@router.get("/review/{finding_id}", response_class=HTMLResponse)
def review_page(
    finding_id: int,
    request: Request,
    token: str = Query(default=""),
    session: Session = Depends(get_session),
):
    finding = _get_finding_or_403(session, finding_id, token)
    asset = session.get(Asset, finding.asset_id)
    evidences = session.exec(
        select(Evidence).where(Evidence.finding_id == finding.id).order_by(Evidence.id)
    ).all()
    return templates.TemplateResponse(
        request,
        "review.html",
        {
            "finding": finding,
            "asset": asset,
            "evidences": evidences,
            "category_label": CATEGORY_LABELS.get(finding.category, finding.category),
            "review_status_label": REVIEW_STATUS_LABELS.get(
                finding.review_status, finding.review_status
            ),
            "token": token,
            "disclaimer": DISCLAIMER,
        },
    )


class ReviewActionIn(BaseModel):
    action: str
    reason: str = ""


@router.post("/api/review/{finding_id}/action")
def review_action(
    finding_id: int,
    payload: ReviewActionIn,
    token: str = Query(default=""),
    session: Session = Depends(get_session),
):
    finding = _get_finding_or_403(session, finding_id, token)

    if payload.action not in (ReviewActionType.confirm.value, ReviewActionType.reject.value):
        raise HTTPException(status_code=400, detail="action 只能是 confirm 或 reject")
    if payload.action == ReviewActionType.reject.value and not payload.reason.strip():
        raise HTTPException(status_code=400, detail="驳回必须填写理由")

    finding.review_status = (
        ReviewStatus.confirmed.value
        if payload.action == ReviewActionType.confirm.value
        else ReviewStatus.rejected.value
    )
    session.add(finding)
    session.add(
        ReviewAction(
            finding_id=finding.id,
            reviewer="web",
            action=payload.action,
            reason=payload.reason.strip(),
        )
    )
    session.commit()
    return {"ok": True, "review_status": finding.review_status}
