"""授权台账 API：上传抽取、人工补录、挂接素材、登记投放上下文（PROMPT §9）。

素材↔授权记录由人工挂接；挂接后按 Campaign 重算状态并给风险信号补 license 证据。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlmodel import Session, select

from app.config import settings
from app.db import get_session
from app.ledger import extract as ledger_extract
from app.ledger import gate
from app.ledger import status as license_status
from app.models import Asset, AssetLicense, Campaign, LicenseRecord

router = APIRouter()

ALLOWED_SUFFIXES = {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".webp", ".bmp"}
MAX_LICENSE_MB = 20


class LicensePatch(BaseModel):
    licensor: str | None = None
    licensee: str | None = None
    scope_type: str | None = None
    scope_items: list[str] | None = None
    territory: list[str] | None = None
    media: list[str] | None = None
    start: str | None = None
    end: str | None = None
    sublicensable: bool | None = None
    restrictions: list[str] | None = None


class LinkIn(BaseModel):
    asset_id: int
    linked_by: str = "web"


class CampaignIn(BaseModel):
    platform: str
    market: str
    start: str | None = None
    end: str | None = None


def _serialize(record: LicenseRecord) -> dict:
    payload = jsonable_encoder(record)
    payload["status_label"] = gate.STATUS_LABELS.get(record.status, record.status)
    return payload


@router.get("/api/licenses")
def list_licenses(session: Session = Depends(get_session)):
    records = session.exec(select(LicenseRecord).order_by(LicenseRecord.id)).all()
    return {"licenses": [_serialize(record) for record in records]}


@router.post("/api/licenses/upload")
async def upload_license(
    file: UploadFile = File(...), session: Session = Depends(get_session)
):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    if len(data) > MAX_LICENSE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"授权文件超过 {MAX_LICENSE_MB}MB 上限")

    suffix = Path(file.filename or "").suffix.casefold()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=415, detail=f"不支持的授权文件类型: {suffix or '无扩展名'}")

    digest = hashlib.sha256(data).hexdigest()
    target_dir = settings.data_dir / "licenses"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{digest}{suffix}"
    target.write_bytes(data)

    extraction = ledger_extract.extract_license(target)
    record = ledger_extract.build_record(extraction, source_doc=str(target))
    session.add(record)
    session.commit()
    session.refresh(record)

    return {
        "record": _serialize(record),
        "text_source": extraction.text.source,
        "missing_fields": extraction.fields.missing_fields,
        "warnings": extraction.warnings,
    }


@router.get("/api/licenses/{license_id}")
def get_license(license_id: int, session: Session = Depends(get_session)):
    record = session.get(LicenseRecord, license_id)
    if record is None:
        raise HTTPException(status_code=404, detail="授权记录不存在")
    return _serialize(record)


@router.patch("/api/licenses/{license_id}")
def patch_license(
    license_id: int, payload: LicensePatch, session: Session = Depends(get_session)
):
    record = session.get(LicenseRecord, license_id)
    if record is None:
        raise HTTPException(status_code=404, detail="授权记录不存在")

    updates = payload.model_dump(exclude_unset=True)
    for name, value in updates.items():
        if name in ("start", "end"):
            parsed = ledger_extract.parse_date(value)
            if value and parsed is None:
                raise HTTPException(status_code=400, detail=f"{name} 日期格式无法解析: {value}")
            setattr(record, name, parsed)
            continue
        setattr(record, name, value)

    # 人工补录视为已核对：补上的关键字段置信记为 1.0
    confidence = dict(record.field_confidence or {})
    for name in updates:
        if name in ("scope_type", "territory", "start", "end"):
            confidence[name] = 1.0
    record.field_confidence = confidence
    record.status = license_status.compute_status(record).status

    session.add(record)
    session.commit()
    session.refresh(record)
    return _serialize(record)


@router.post("/api/licenses/{license_id}/link")
def link_license(
    license_id: int, payload: LinkIn, session: Session = Depends(get_session)
):
    record = session.get(LicenseRecord, license_id)
    if record is None:
        raise HTTPException(status_code=404, detail="授权记录不存在")
    asset = session.get(Asset, payload.asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="素材不存在")

    existing = session.exec(
        select(AssetLicense).where(
            AssetLicense.asset_id == payload.asset_id,
            AssetLicense.license_id == license_id,
        )
    ).first()
    if existing is None:
        session.add(
            AssetLicense(
                asset_id=payload.asset_id,
                license_id=license_id,
                linked_by=payload.linked_by,
            )
        )
        session.commit()

    summary = gate.apply_to_asset(session, payload.asset_id)
    return {"ok": True, "link": summary}


@router.post("/api/assets/{asset_id}/campaign")
def set_campaign(
    asset_id: int, payload: CampaignIn, session: Session = Depends(get_session)
):
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    if not payload.platform.strip() or not payload.market.strip():
        raise HTTPException(status_code=400, detail="platform 与 market 必填")

    campaign = gate.get_campaign(session, asset_id)
    if campaign is None:
        campaign = Campaign(asset_id=asset_id)
    campaign.platform = payload.platform.strip()
    campaign.market = payload.market.strip()
    campaign.start = ledger_extract.parse_date(payload.start)
    campaign.end = ledger_extract.parse_date(payload.end)
    session.add(campaign)
    session.commit()
    session.refresh(campaign)

    summary = gate.apply_to_asset(session, asset_id)
    return {"ok": True, "campaign": jsonable_encoder(campaign), "gate": summary}


@router.get("/api/assets/{asset_id}/ledger")
def asset_ledger(asset_id: int, session: Session = Depends(get_session)):
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="素材不存在")

    context = gate.ledger_context(session, asset_id)
    return {
        "asset_id": asset_id,
        "campaign": jsonable_encoder(context["campaign"]),
        "campaign_missing": context["campaign_missing"],
        "licenses": [
            {
                "record": _serialize(item["record"]),
                "status": item["result"].status,
                "status_label": item["status_label"],
                "reasons": item["result"].reasons,
                "linked_at": item["linked_at"],
            }
            for item in context["items"]
        ],
        "unlinked": [_serialize(record) for record in context["available"]],
    }
