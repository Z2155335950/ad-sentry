"""演示模式：预置完整结果回放（PROMPT §16）。

`fixtures/demo/scenarios.json` 是唯一的演示数据源；回放时按需把素材、任务、信号、
授权记录幂等写入数据库，因此断网也能走完整卡片与复核页。演示素材不设 TTL。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date
from functools import lru_cache
from pathlib import Path

from sqlmodel import Session, select

from app.ledger import gate
from app.models import (
    LEVEL_ORDER,
    Asset,
    AssetLicense,
    Campaign,
    Detection,
    Evidence,
    Finding,
    Job,
    JobStatus,
    LicenseRecord,
    LicenseStatus,
    ReviewStatus,
    utcnow,
)
from app.pipeline import fuse

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_PATH = ROOT / "fixtures" / "demo" / "scenarios.json"
LEVELS = ("P0", "P1", "P2", "P3")


@lru_cache(maxsize=1)
def load_scenarios() -> tuple[dict, ...]:
    payload = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
    return tuple(payload.get("scenarios") or [])


def get_scenario(scenario_id: str) -> dict | None:
    return next((item for item in load_scenarios() if item["id"] == scenario_id), None)


def public_scenarios() -> list[dict]:
    return [
        {
            "id": item["id"],
            "kind": item.get("kind", "asset"),
            "title": item["title"],
            "summary": item.get("summary", ""),
            "expected": item.get("expected", ""),
        }
        for item in load_scenarios()
    ]


def _parse_date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _fixture_path(relative: str) -> Path:
    path = Path(relative)
    return path if path.is_absolute() else ROOT / path


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _image_size(path: Path) -> tuple[int | None, int | None]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return image.size
    except Exception:
        return None, None


def _ensure_asset(session: Session, scenario: dict) -> Asset:
    path = _fixture_path(scenario["media"])
    if not path.exists():
        raise FileNotFoundError(f"演示素材不存在：{path}")
    digest = _file_sha256(path)
    asset = session.exec(select(Asset).where(Asset.sha256 == digest)).first()
    if asset is None:
        width, height = _image_size(path) if scenario["media_type"] == "image" else (None, None)
        asset = Asset(
            sha256=digest,
            media_type=scenario["media_type"],
            path=str(path),
            width=width,
            height=height,
            expires_at=None,
        )
        session.add(asset)
        session.flush()
    elif not Path(asset.path).exists():
        # 记录还在但文件被清理过：指回演示素材，且不设 TTL
        asset.path = str(path)
        asset.expires_at = None
        session.add(asset)
    return asset


def _ensure_job(session: Session, asset: Asset, scenario_id: str) -> Job:
    stage = f"demo:{scenario_id}"
    job = session.exec(
        select(Job).where(Job.asset_id == asset.id, Job.stage == stage)
    ).first()
    if job is None:
        job = Job(
            asset_id=asset.id,
            status=JobStatus.done.value,
            stage=stage,
            started_at=utcnow(),
            finished_at=utcnow(),
        )
        session.add(job)
        session.flush()
    return job


def _seed_findings(
    session: Session, asset: Asset, job: Job, scenario: dict
) -> None:
    for item in scenario.get("findings") or []:
        key = item.get("dedupe_key") or f"{item['category']}:{item.get('suggestion', '')[:40]}"
        existing = session.exec(
            select(Finding).where(Finding.asset_id == asset.id, Finding.dedupe_key == key)
        ).first()
        if existing is not None:
            continue

        level, downgraded = fuse.cap_level_without_anchor(
            item["level"], item.get("evidence", [])
        )
        suggestion = item.get("suggestion", "")
        if downgraded:
            suggestion = f"{suggestion}（缺少参照物证据，按红线最高 P2）"

        session.add(
            Detection(
                job_id=job.id,
                asset_id=asset.id,
                tool=item.get("tool", "demo"),
                raw=item,
                confidence=item.get("confidence"),
                cost_usd=0.0,
            )
        )
        finding = Finding(
            asset_id=asset.id,
            category=item["category"],
            level=level,
            confidence=item.get("confidence"),
            suggestion=suggestion,
            review_status=ReviewStatus.pending.value,
            dedupe_key=key,
        )
        session.add(finding)
        session.flush()
        for ev in item.get("evidence", []):
            session.add(
                Evidence(
                    finding_id=finding.id,
                    kind=ev["kind"],
                    ref_url=ev.get("ref_url"),
                    ref_record_id=ev.get("ref_record_id"),
                    bbox=ev.get("bbox"),
                    t_start=ev.get("t_start"),
                    t_end=ev.get("t_end"),
                    clause_id=ev.get("clause_id"),
                    note=ev.get("note"),
                )
            )
    session.commit()


def _seed_campaign(session: Session, asset: Asset, scenario: dict) -> Campaign | None:
    payload = scenario.get("campaign")
    if not payload:
        return None
    campaign = session.exec(
        select(Campaign).where(Campaign.asset_id == asset.id)
    ).first()
    if campaign is None:
        campaign = Campaign(asset_id=asset.id)
    campaign.platform = payload.get("platform", "")
    campaign.market = payload.get("market", "")
    campaign.start = _parse_date(payload.get("start"))
    campaign.end = _parse_date(payload.get("end"))
    session.add(campaign)
    session.commit()
    return campaign


def _seed_licenses(session: Session, asset: Asset, scenario: dict) -> list[LicenseRecord]:
    records: list[LicenseRecord] = []
    for item in scenario.get("licenses") or []:
        source_doc = item.get("source_doc", "")
        record = session.exec(
            select(LicenseRecord).where(LicenseRecord.source_doc == source_doc)
        ).first()
        if record is None:
            record = LicenseRecord(
                source_doc=source_doc,
                status=LicenseStatus.effective.value,
            )
        record.licensor = item.get("licensor", "")
        record.licensee = item.get("licensee", "")
        record.scope_type = item.get("scope_type", "")
        record.scope_items = item.get("scope_items") or []
        record.territory = item.get("territory") or []
        record.media = item.get("media") or []
        record.start = _parse_date(item.get("start"))
        record.end = _parse_date(item.get("end"))
        record.sublicensable = item.get("sublicensable")
        record.restrictions = item.get("restrictions") or []
        record.field_confidence = item.get("field_confidence") or {}
        session.add(record)
        session.flush()

        linked = session.exec(
            select(AssetLicense).where(
                AssetLicense.asset_id == asset.id,
                AssetLicense.license_id == record.id,
            )
        ).first()
        if linked is None:
            session.add(
                AssetLicense(
                    asset_id=asset.id,
                    license_id=record.id,
                    linked_by="demo",
                )
            )
        records.append(record)
    session.commit()
    return records


def _scenario_keys(scenario: dict) -> set[str]:
    return {
        item.get("dedupe_key")
        or f"{item['category']}:{item.get('suggestion', '')[:40]}"
        for item in scenario.get("findings") or []
    }


def findings_payload(session: Session, asset_id: int) -> list[dict]:
    rows = session.exec(
        select(Finding).where(Finding.asset_id == asset_id).order_by(Finding.id)
    ).all()
    payload = []
    for finding in rows:
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
    payload.sort(key=lambda item: (LEVEL_ORDER.get(item["level"], 9), item["id"]))
    return payload


def ensure_scenario(session: Session, scenario: dict) -> dict:
    if scenario.get("kind") == "batch":
        return aggregate(session)

    asset = _ensure_asset(session, scenario)
    job = _ensure_job(session, asset, scenario["id"])
    _seed_findings(session, asset, job, scenario)
    _seed_campaign(session, asset, scenario)
    licenses = _seed_licenses(session, asset, scenario)
    if licenses:
        # 过期 / 地域 / 媒介 / 范围不符会补 license 证据并抬升等级（只升不降）
        gate.apply_to_asset(session, asset.id)
    session.commit()
    session.refresh(asset)
    session.refresh(job)
    allowed = _scenario_keys(scenario)
    findings = [
        item
        for item in findings_payload(session, asset.id)
        if item["dedupe_key"] in allowed
    ]
    return {
        "scenario": scenario,
        "asset": asset,
        "job": job,
        "findings": findings,
        "ledger": gate.ledger_context(session, asset.id),
    }


def aggregate(session: Session) -> dict:
    rows: list[dict] = []
    totals = {level: 0 for level in LEVELS}
    asset_scenarios = [item for item in load_scenarios() if item.get("kind") != "batch"]
    for scenario in asset_scenarios:
        seeded = ensure_scenario(session, scenario)
        findings = seeded["findings"]
        counts = {level: 0 for level in LEVELS}
        for finding in findings:
            counts[finding["level"]] = counts.get(finding["level"], 0) + 1
        for level in LEVELS:
            totals[level] += counts[level]
        top = min(
            (finding["level"] for finding in findings),
            key=LEVEL_ORDER.__getitem__,
            default=None,
        )
        rows.append(
            {
                "id": scenario["id"],
                "title": scenario["title"],
                "asset_id": seeded["asset"].id,
                "job_id": seeded["job"].id,
                "top_level": top,
                "counts": counts,
                "finding_count": len(findings),
                "needs_action": counts["P0"] + counts["P1"],
            }
        )
    return {
        "assets": len(rows),
        "findings": sum(row["finding_count"] for row in rows),
        "needs_action": sum(row["needs_action"] for row in rows),
        "totals": totals,
        "rows": rows,
    }
