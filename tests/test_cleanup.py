"""TTL 清理单测：过期素材级联删除，未过期素材与引用中的授权文件保留。"""

from __future__ import annotations

import os
import time
from datetime import timedelta

from app.models import (
    Asset,
    AssetLicense,
    Campaign,
    Detection,
    Evidence,
    FeishuEvent,
    Finding,
    Job,
    LicenseRecord,
    ReviewAction,
    ToolCall,
    utcnow,
)
from app.workers.cleanup import cleanup_expired
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select


def _session(tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine, Session(engine)


def _make_asset(session, tmp_path, sha, expires_at, name):
    path = tmp_path / "assets" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    asset = Asset(sha256=sha, media_type="image", path=str(path), expires_at=expires_at)
    session.add(asset)
    session.flush()
    return asset, path


def test_cleanup_cascades_expired_asset(tmp_path):
    _, session = _session(tmp_path)
    expired, expired_file = _make_asset(
        session, tmp_path, "a" * 64, utcnow() - timedelta(hours=1), "old.png"
    )
    fresh, fresh_file = _make_asset(
        session, tmp_path, "b" * 64, utcnow() + timedelta(hours=1), "fresh.png"
    )
    extracted = tmp_path / "extracted" / expired.sha256 / "frames"
    extracted.mkdir(parents=True)
    (extracted / "f.jpg").write_bytes(b"f")

    job = Job(asset_id=expired.id, status="done")
    session.add(job)
    session.flush()
    finding = Finding(asset_id=expired.id, category="logo", level="P1", dedupe_key="logo:x")
    session.add(finding)
    session.flush()
    session.add(Evidence(finding_id=finding.id, kind="reference"))
    session.add(ReviewAction(finding_id=finding.id, action="confirm"))
    session.add(Detection(job_id=job.id, asset_id=expired.id, tool="logo"))
    session.add(ToolCall(job_id=job.id, tool="logo"))
    session.add(FeishuEvent(message_id="om_cleanup", job_id=job.id))
    session.add(Campaign(asset_id=expired.id, platform="tiktok"))
    session.commit()

    result = cleanup_expired(session=session, data_dir=tmp_path)

    assert result["assets"] == 1
    assert result["files"] == 1
    assert result["extracted_dirs"] == 1
    assert not expired_file.exists()
    assert not (tmp_path / "extracted" / expired.sha256).exists()
    assert fresh_file.exists()

    assert session.exec(select(Asset)).all()[0].id == fresh.id
    for model in (
        Finding,
        Evidence,
        ReviewAction,
        Detection,
        Job,
        AssetLicense,
        Campaign,
        ToolCall,
        FeishuEvent,
    ):
        assert session.exec(select(model)).all() == []
    session.close()


def test_cleanup_removes_only_orphan_license_files(tmp_path):
    _, session = _session(tmp_path)
    licenses = tmp_path / "licenses"
    licenses.mkdir()
    referenced = licenses / "kept.pdf"
    orphan = licenses / "orphan.pdf"
    referenced.write_bytes(b"kept")
    orphan.write_bytes(b"orphan")
    old = time.time() - 48 * 3600
    os.utime(referenced, (old, old))
    os.utime(orphan, (old, old))

    session.add(LicenseRecord(source_doc=str(referenced), scope_type="music"))
    session.commit()

    result = cleanup_expired(session=session, data_dir=tmp_path)

    assert result["license_files"] == 1
    assert referenced.exists()
    assert not orphan.exists()
    assert session.exec(select(LicenseRecord)).all()
    session.close()
