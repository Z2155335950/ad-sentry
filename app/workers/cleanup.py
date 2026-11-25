"""TTL 清理：过期素材、抽取产物、授权文件与关联记录（PROMPT §14）。

只删除 `DATA_DIR` 下的文件；演示回放引用的 `fixtures/` 素材 expires_at 为空，
不会被清理。授权文件按 mtime 清理未被任何 LicenseRecord 引用的孤儿文件。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete
from sqlmodel import Session, select

from app.config import settings
from app.db import engine, init_db
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

logger = logging.getLogger(__name__)


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _unlink(path: Path, root: Path) -> bool:
    if not _under(path, root):
        return False
    if path.is_file():
        path.unlink(missing_ok=True)
        return True
    return False


def _remove_tree(path: Path, root: Path) -> bool:
    if not _under(path, root) or not path.exists():
        return False
    shutil.rmtree(path, ignore_errors=True)
    return True


def _cleanup_orphan_licenses(session: Session, data_root: Path, cutoff_ts: float) -> int:
    license_dir = data_root / "licenses"
    if not license_dir.exists():
        return 0
    referenced = {
        Path(record.source_doc).resolve()
        for record in session.exec(select(LicenseRecord)).all()
        if record.source_doc
    }
    removed = 0
    for path in license_dir.iterdir():
        if not path.is_file() or path.resolve() in referenced:
            continue
        try:
            if path.stat().st_mtime > cutoff_ts:
                continue
        except OSError:
            continue
        path.unlink(missing_ok=True)
        removed += 1
    return removed


def cleanup_expired(
    *,
    now: datetime | None = None,
    data_dir: str | Path | None = None,
    session: Session | None = None,
) -> dict:
    """删除已过期素材及其全部子记录与文件，返回计数供验收断言。"""
    now = now or utcnow()
    data_root = Path(data_dir or settings.data_dir).resolve()
    own_session = session is None
    session = session or Session(engine)

    removed = {
        "assets": 0,
        "files": 0,
        "extracted_dirs": 0,
        "license_files": 0,
        "sha256": [],
    }
    try:
        assets = session.exec(
            select(Asset).where(
                Asset.expires_at.is_not(None), Asset.expires_at <= now
            )
        ).all()
        for asset in assets:
            job_ids = [
                job.id
                for job in session.exec(select(Job).where(Job.asset_id == asset.id)).all()
                if job.id is not None
            ]
            finding_ids = [
                finding.id
                for finding in session.exec(
                    select(Finding).where(Finding.asset_id == asset.id)
                ).all()
                if finding.id is not None
            ]

            # 记录在 commit 后不可再访问，先取出文件信息
            asset_path = Path(asset.path)
            sha256 = asset.sha256

            # 没有 relationship 定义，按外键顺序显式 DELETE，避免先删父表
            if finding_ids:
                session.execute(
                    delete(ReviewAction).where(ReviewAction.finding_id.in_(finding_ids))
                )
                session.execute(
                    delete(Evidence).where(Evidence.finding_id.in_(finding_ids))
                )
            session.execute(delete(Finding).where(Finding.asset_id == asset.id))
            session.execute(delete(Detection).where(Detection.asset_id == asset.id))
            if job_ids:
                session.execute(delete(ToolCall).where(ToolCall.job_id.in_(job_ids)))
                session.execute(
                    delete(FeishuEvent).where(FeishuEvent.job_id.in_(job_ids))
                )
            session.execute(delete(Job).where(Job.asset_id == asset.id))
            session.execute(
                delete(AssetLicense).where(AssetLicense.asset_id == asset.id)
            )
            session.execute(delete(Campaign).where(Campaign.asset_id == asset.id))
            session.execute(delete(Asset).where(Asset.id == asset.id))
            session.commit()

            if _unlink(asset_path, data_root):
                removed["files"] += 1
            if _remove_tree(data_root / "extracted" / sha256, data_root):
                removed["extracted_dirs"] += 1
            removed["assets"] += 1
            removed["sha256"].append(sha256)

        cutoff_ts = time.time() - settings.asset_ttl_hours * 3600
        removed["license_files"] = _cleanup_orphan_licenses(session, data_root, cutoff_ts)
        session.commit()
    finally:
        if own_session:
            session.close()
    return removed


async def cleanup_loop(stop: asyncio.Event, interval_seconds: float | None = None) -> None:
    interval = settings.cleanup_interval_seconds if interval_seconds is None else interval_seconds
    logger.info("cleanup loop started (interval=%ss)", interval)
    while not stop.is_set():
        try:
            result = await asyncio.to_thread(cleanup_expired)
            if result["assets"] or result["license_files"]:
                logger.info("cleanup: %s", result)
        except Exception:
            logger.exception("cleanup failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass
    logger.info("cleanup loop stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="AdSentry TTL 清理")
    parser.add_argument("--loop", action="store_true", help="常驻循环（默认只跑一次）")
    args = parser.parse_args()

    init_db()
    if not args.loop:
        print(json.dumps(cleanup_expired(), ensure_ascii=False, indent=2))
        return

    async def run() -> None:
        stop = asyncio.Event()
        await cleanup_loop(stop)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
