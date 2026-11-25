"""把授权台账接到素材与风险信号上（PROMPT §9）。

MVP 里「素材 ↔ 授权记录」由人工在复核页挂接，实体自动对齐不进 MVP。这里只负责：

- 按该素材的投放上下文（Campaign）重算每条授权记录的状态；
- 给授权类型覆盖到的风险类别补一条 `license` 证据；
- 授权过期 / 地域不符 / 媒介不符 / 范围不符时把等级抬到 P1 —— 只升不降，
  授权有效不会自动抹掉其他工具产出的风险。
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.ledger import status as license_status
from app.models import (
    LEVEL_ORDER,
    AssetLicense,
    Campaign,
    Evidence,
    Finding,
    LicenseRecord,
    LicenseStatus,
)

STATUS_LABELS = {
    LicenseStatus.effective.value: "授权有效",
    LicenseStatus.expiring_soon.value: "授权即将到期",
    LicenseStatus.expired.value: "授权已过期",
    LicenseStatus.out_of_territory.value: "授权地域不符",
    LicenseStatus.out_of_media.value: "授权媒介不符",
    LicenseStatus.out_of_scope.value: "授权范围不符",
    LicenseStatus.needs_review.value: "授权待人工录入",
}


def get_campaign(session: Session, asset_id: int) -> Campaign | None:
    return session.exec(select(Campaign).where(Campaign.asset_id == asset_id)).first()


def linked_records(session: Session, asset_id: int) -> list[tuple[AssetLicense, LicenseRecord]]:
    links = session.exec(
        select(AssetLicense).where(AssetLicense.asset_id == asset_id).order_by(AssetLicense.id)
    ).all()
    pairs: list[tuple[AssetLicense, LicenseRecord]] = []
    for link in links:
        record = session.get(LicenseRecord, link.license_id)
        if record is not None:
            pairs.append((link, record))
    return pairs


def _note(record: LicenseRecord, result: license_status.StatusResult) -> str:
    label = STATUS_LABELS.get(result.status, result.status)
    detail = "；".join(result.reasons)
    scope = f"{record.scope_type or '未标注范围'}"
    return f"{label}（{scope}）" + (f"：{detail}" if detail else "")


def _upsert_evidence(session: Session, finding: Finding, record: LicenseRecord, note: str) -> bool:
    ref = f"license:{record.id}"
    existing = session.exec(
        select(Evidence).where(
            Evidence.finding_id == finding.id,
            Evidence.kind == "license",
            Evidence.ref_record_id == ref,
        )
    ).first()
    if existing is not None:
        changed = existing.note != note
        existing.note = note
        session.add(existing)
        return changed
    session.add(
        Evidence(
            finding_id=finding.id,
            kind="license",
            ref_record_id=ref,
            note=note,
        )
    )
    return True


def apply_to_asset(session: Session, asset_id: int) -> dict:
    """重算状态并回写证据/等级。返回本次变更摘要，供 API 与验收脚本断言。"""
    campaign = get_campaign(session, asset_id)
    records = linked_records(session, asset_id)
    findings = session.exec(select(Finding).where(Finding.asset_id == asset_id)).all()

    raised: list[int] = []
    matched: list[dict] = []

    for _link, record in records:
        record_result = license_status.compute_status(record, campaign)
        record.status = record_result.status
        session.add(record)

        for finding in findings:
            covered = license_status.covers_category(record.scope_type, finding.category)
            if not covered:
                continue
            result = license_status.compute_status(record, campaign, category=finding.category)
            note = _note(record, result)
            _upsert_evidence(session, finding, record, note)
            matched.append(
                {
                    "finding_id": finding.id,
                    "license_id": record.id,
                    "status": result.status,
                }
            )
            if result.is_risk and LEVEL_ORDER[finding.level] > LEVEL_ORDER["P1"]:
                finding.level = "P1"
                session.add(finding)
                raised.append(finding.id)

    session.commit()
    return {
        "campaign": campaign.model_dump() if campaign else None,
        "matched": matched,
        "raised_findings": sorted(set(raised)),
    }


def ledger_context(session: Session, asset_id: int) -> dict:
    """复核页/API 用的台账视图。"""
    campaign = get_campaign(session, asset_id)
    pairs = linked_records(session, asset_id)
    linked_ids = {record.id for _link, record in pairs}

    items = []
    for link, record in pairs:
        result = license_status.compute_status(record, campaign)
        items.append(
            {
                "record": record,
                "result": result,
                "status_label": STATUS_LABELS.get(result.status, result.status),
                "linked_at": link.linked_at,
                "linked_by": link.linked_by,
            }
        )

    available = [
        record
        for record in session.exec(select(LicenseRecord).order_by(LicenseRecord.id)).all()
        if record.id not in linked_ids
    ]
    return {
        "campaign": campaign,
        "campaign_missing": campaign is None,
        "items": items,
        "available": available,
    }
