"""上传入口与任务查询。飞书入口在 Phase 5 接入，本地 Web 上传是降级入口。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.api.review import DISCLAIMER, make_review_url
from app.config import settings
from app.db import get_session
from app.models import LEVEL_ORDER, Asset, Evidence, Finding, Job
from app.pipeline import extract
from app.pipeline.ingest import UnsupportedMedia, store_asset
from app.pipeline.report import build_card

BASE_DIR = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def upload_page(request: Request):
    return templates.TemplateResponse(
        request,
        "upload.html",
        {"disclaimer": DISCLAIMER, "max_upload_mb": settings.max_upload_mb},
    )


@router.get("/api/health")
def health():
    return {"ok": True}


@router.post("/api/upload")
async def upload(file: UploadFile = File(...), session: Session = Depends(get_session)):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"文件超过 {settings.max_upload_mb}MB 上限")
    try:
        asset, job = store_asset(data, file.filename, file.content_type, session)
    except UnsupportedMedia as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    return {"asset_id": asset.id, "job_id": job.id}


@router.get("/api/jobs/{job_id}")
def job_status(
    job_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    asset = session.get(Asset, job.asset_id)

    findings = session.exec(
        select(Finding).where(Finding.asset_id == job.asset_id).order_by(Finding.id)
    ).all()
    payload = []
    for finding in findings:
        evidences = session.exec(
            select(Evidence).where(Evidence.finding_id == finding.id).order_by(Evidence.id)
        ).all()
        payload.append(
            {
                "id": finding.id,
                "category": finding.category,
                "level": finding.level,
                "confidence": finding.confidence,
                "suggestion": finding.suggestion,
                "review_status": finding.review_status,
                "dedupe_key": finding.dedupe_key,
                "evidence": [ev.model_dump() for ev in evidences],
            }
        )
    payload.sort(key=lambda f: (LEVEL_ORDER.get(f["level"], 9), f["id"]))

    card = None
    if payload:
        review_urls = {f["id"]: make_review_url(request, f["id"]) for f in payload}
        card = build_card(payload, review_urls)

    return {
        "job": job.model_dump(),
        "asset": asset.model_dump() if asset else None,
        "extraction_available": bool(asset and extract.has_extraction(asset.sha256)),
        "findings": payload,
        "card": card,
    }


@router.get("/api/jobs/{job_id}/extraction")
def job_extraction(job_id: int, session: Session = Depends(get_session)):
    """抽取层产物（帧、OCR、转写），供工具层调试与后续证据链使用。"""
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    asset = session.get(Asset, job.asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    data = extract.load_extraction(asset.sha256)
    if data is None:
        raise HTTPException(status_code=404, detail="抽取结果尚未生成")
    return data
