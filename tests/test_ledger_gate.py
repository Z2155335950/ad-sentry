"""授权挂接：补 license 证据、风险只升不降、重复挂接幂等。"""

from datetime import date

from app.db import engine, init_db
from app.ledger import gate
from app.models import (
    Asset,
    AssetLicense,
    Campaign,
    Evidence,
    Finding,
    LicenseRecord,
)
from sqlmodel import Session, select


def _setup(session: Session, tag: str, category: str, level: str, record: LicenseRecord):
    asset = Asset(sha256=f"gate-{tag}", media_type="image", path="x.png")
    session.add(asset)
    session.commit()
    session.refresh(asset)

    finding = Finding(
        asset_id=asset.id,
        category=category,
        level=level,
        confidence=0.8,
        suggestion="测试信号",
        dedupe_key=f"{category}:{tag}",
    )
    session.add(finding)
    session.add(record)
    session.commit()
    session.refresh(finding)
    session.refresh(record)

    session.add(AssetLicense(asset_id=asset.id, license_id=record.id, linked_by="test"))
    session.commit()
    return asset, finding, record


def _music_record(**overrides) -> LicenseRecord:
    defaults = {
        "scope_type": "music",
        "territory": ["东南亚"],
        "media": ["TikTok"],
        "start": date(2026, 1, 1),
        "end": date(2027, 12, 31),
        "source_doc": "licence.pdf",
        "field_confidence": {},
    }
    defaults.update(overrides)
    return LicenseRecord(**defaults)


def test_expired_license_raises_p2_finding_to_p1():
    init_db()
    with Session(engine) as session:
        record = _music_record(end=date(2025, 6, 30))
        asset, finding, record = _setup(session, "expired", "bgm", "P2", record)

        summary = gate.apply_to_asset(session, asset.id)
        session.refresh(finding)

        assert finding.level == "P1"
        assert finding.id in summary["raised_findings"]
        evidence = session.exec(
            select(Evidence).where(
                Evidence.finding_id == finding.id,
                Evidence.kind == "license",
            )
        ).all()
        assert len(evidence) == 1
        assert "已过期" in evidence[0].note


def test_effective_license_never_lowers_level():
    init_db()
    with Session(engine) as session:
        record = _music_record()
        asset, finding, record = _setup(session, "effective", "bgm", "P0", record)
        session.add(
            Campaign(asset_id=asset.id, platform="tiktok", market="SEA")
        )
        session.commit()

        gate.apply_to_asset(session, asset.id)
        session.refresh(finding)

        assert finding.level == "P0"
        evidence = session.exec(
            select(Evidence).where(Evidence.finding_id == finding.id)
        ).all()
        assert any("授权有效" in (ev.note or "") for ev in evidence)


def test_out_of_territory_under_campaign_raises_and_is_idempotent():
    init_db()
    with Session(engine) as session:
        record = _music_record(territory=["美国", "欧盟"])
        asset, finding, record = _setup(session, "territory", "bgm", "P2", record)
        session.add(Campaign(asset_id=asset.id, platform="tiktok", market="SEA"))
        session.commit()

        gate.apply_to_asset(session, asset.id)
        gate.apply_to_asset(session, asset.id)
        session.refresh(finding)

        assert finding.level == "P1"
        evidence = session.exec(
            select(Evidence).where(
                Evidence.finding_id == finding.id,
                Evidence.kind == "license",
            )
        ).all()
        assert len(evidence) == 1
        assert "地域不符" in evidence[0].note


def test_license_scope_mismatch_is_not_applied():
    init_db()
    with Session(engine) as session:
        record = _music_record()
        asset, finding, record = _setup(session, "scope", "logo", "P2", record)

        gate.apply_to_asset(session, asset.id)
        session.refresh(finding)

        assert finding.level == "P2"
        evidence = session.exec(
            select(Evidence).where(Evidence.finding_id == finding.id)
        ).all()
        assert evidence == []
