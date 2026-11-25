"""Job 队列：认领任务 → 抽取层（Phase 1）→ 占位融合层 → 完成。

Phase 0 的占位融合把 fixtures/fake_findings.json 当作工具层输出写入 Detection /
Finding / Evidence；真实工具适配器在 Phase 2–3 替换这里的调用点，业务接口保持不变。
"""

from __future__ import annotations

import asyncio
import json
import logging

from sqlmodel import Session, select

from app.config import settings
from app.db import engine
from app.models import (
    Asset,
    Detection,
    Evidence,
    Finding,
    Job,
    JobStatus,
    ReviewStatus,
    utcnow,
)
from app.pipeline import extract, fuse

logger = logging.getLogger(__name__)


def _load_fixture() -> dict:
    path = settings.fixtures_dir / "fake_findings.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _set_stage(job_id: int, stage: str) -> None:
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job is not None:
            job.stage = stage
            session.add(job)
            session.commit()


def _dedupe_key(item: dict) -> str:
    if item.get("dedupe_key"):
        return str(item["dedupe_key"])
    for ev in item.get("evidence", []):
        for field in ("ref_record_id", "clause_id", "ref_url"):
            if ev.get(field):
                return f"{item['category']}:{ev[field]}"
    return f"{item['category']}:{item.get('suggestion', '')[:40]}"


def _apply_fixture(session: Session, job: Job, asset: Asset, fixture: dict) -> None:
    for item in fixture.get("findings", []):
        key = _dedupe_key(item)
        existing = session.exec(
            select(Finding).where(Finding.asset_id == asset.id, Finding.dedupe_key == key)
        ).first()
        if existing is not None:
            continue

        level, downgraded = fuse.cap_level_without_anchor(item["level"], item.get("evidence", []))
        suggestion = item.get("suggestion", "")
        if downgraded:
            suggestion = f"{suggestion}（缺少参照物证据，按红线最高 P2）"

        session.add(
            Detection(
                job_id=job.id,
                asset_id=asset.id,
                tool=item.get("tool", "fixture"),
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


def claim_next_job() -> int | None:
    # 单 worker 场景；多 worker 时需改为带 WHERE status=queued 的条件更新。
    with Session(engine) as session:
        job = session.exec(
            select(Job).where(Job.status == JobStatus.queued.value).order_by(Job.id)
        ).first()
        if job is None:
            return None
        job.status = JobStatus.running.value
        job.stage = "extract" if settings.extract_enabled else "fuse"
        job.started_at = utcnow()
        session.add(job)
        session.commit()
        return job.id


def process_job(job_id: int) -> None:
    try:
        fixture = _load_fixture()
        with Session(engine) as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            asset = session.get(Asset, job.asset_id)
            if asset is None:
                raise RuntimeError(f"asset {job.asset_id} 不存在")

            if settings.extract_enabled:
                job.stage = "extract"
                session.add(job)
                session.commit()

                result = extract.extract_asset(
                    asset,
                    on_stage=lambda stage: _set_stage(job_id, f"extract:{stage}"),
                )
                media = result.get("media") or {}
                if media.get("duration") is not None:
                    asset.duration = float(media["duration"])
                if media.get("width") and media.get("height"):
                    asset.width = int(media["width"])
                    asset.height = int(media["height"])
                session.add(asset)
                session.add(
                    Detection(
                        job_id=job.id,
                        asset_id=asset.id,
                        tool="extract",
                        raw=extract.summary(result),
                    )
                )
                session.commit()

            job.stage = "fuse"
            session.add(job)
            session.commit()

            _apply_fixture(session, job, asset, fixture)

            job.status = JobStatus.done.value
            job.stage = "done"
            job.finished_at = utcnow()
            session.add(job)
            session.commit()
    except Exception as exc:  # 单任务失败不拖垮 worker
        logger.exception("job %s failed", job_id)
        with Session(engine) as session:
            job = session.get(Job, job_id)
            if job is not None:
                job.status = JobStatus.failed.value
                job.error = str(exc)[:500]
                job.finished_at = utcnow()
                session.add(job)
                session.commit()


async def worker_loop(stop: asyncio.Event, poll_seconds: float = 0.5) -> None:
    logger.info("worker started")
    while not stop.is_set():
        job_id = await asyncio.to_thread(claim_next_job)
        if job_id is None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
            except TimeoutError:
                pass
            continue
        await asyncio.to_thread(process_job, job_id)
    logger.info("worker stopped")
