"""Phase 7 验收（等价于 `make p7`）：TTL 清理 + 部署产物。

清理验证覆盖：过期素材的 DB 记录级联删除、素材文件、抽取目录、孤儿授权文件；
未过期素材与已被 LicenseRecord 引用的授权文件必须保留。
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "p7_check_data"
DB_PATH = DATA_DIR / "p7_check.db"

os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH.as_posix()}"
os.environ["DATA_DIR"] = str(DATA_DIR)
os.environ["DEMO_MODE"] = "true"
os.environ["WORKER_IN_APP"] = "false"

from app.db import engine, init_db  # noqa: E402
from app.models import (  # noqa: E402
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
from app.workers.cleanup import cleanup_expired  # noqa: E402
from sqlmodel import Session, select  # noqa: E402


def fail(message: str) -> None:
    print(f"[FAIL] {message}")
    sys.exit(1)


def seed() -> tuple[Path, Path, Path, Path]:
    assets_dir = DATA_DIR / "assets"
    licenses_dir = DATA_DIR / "licenses"
    assets_dir.mkdir(parents=True, exist_ok=True)
    licenses_dir.mkdir(parents=True, exist_ok=True)

    old = assets_dir / "old.png"
    fresh = assets_dir / "fresh.png"
    old.write_bytes(b"old")
    fresh.write_bytes(b"fresh")
    old_sha = "a" * 64
    fresh_sha = "b" * 64
    (DATA_DIR / "extracted" / old_sha / "frames").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "extracted" / old_sha / "frames" / "f.jpg").write_bytes(b"frame")

    kept_license = licenses_dir / "kept.pdf"
    orphan_license = licenses_dir / "orphan.pdf"
    kept_license.write_bytes(b"kept")
    orphan_license.write_bytes(b"orphan")
    old_ts = time.time() - 48 * 3600
    os.utime(kept_license, (old_ts, old_ts))
    os.utime(orphan_license, (old_ts, old_ts))

    with Session(engine) as session:
        expired = Asset(
            sha256=old_sha,
            media_type="image",
            path=str(old),
            expires_at=utcnow() - timedelta(hours=1),
        )
        alive = Asset(
            sha256=fresh_sha,
            media_type="image",
            path=str(fresh),
            expires_at=utcnow() + timedelta(hours=24),
        )
        session.add(expired)
        session.add(alive)
        session.flush()

        job = Job(asset_id=expired.id, status="done", stage="p7")
        session.add(job)
        session.flush()

        finding = Finding(
            asset_id=expired.id,
            category="logo",
            level="P1",
            dedupe_key="logo:p7",
        )
        session.add(finding)
        session.flush()
        session.add(
            Evidence(finding_id=finding.id, kind="reference", note="p7 evidence")
        )
        session.add(
            ReviewAction(finding_id=finding.id, action="confirm", reason="p7")
        )
        session.add(
            Detection(job_id=job.id, asset_id=expired.id, tool="logo", raw={})
        )
        session.add(ToolCall(job_id=job.id, tool="logo"))
        session.add(FeishuEvent(message_id="om_p7", job_id=job.id))
        session.add(Campaign(asset_id=expired.id, platform="tiktok", market="SEA"))

        record = LicenseRecord(source_doc=str(kept_license), scope_type="music")
        session.add(record)
        session.flush()
        session.add(AssetLicense(asset_id=expired.id, license_id=record.id))
        session.commit()

    return old, fresh, kept_license, orphan_license


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    init_db()

    old, fresh, kept_license, orphan_license = seed()
    result = cleanup_expired()
    if result["assets"] != 1:
        fail(f"应清理 1 个过期素材，实际 {result['assets']}: {result}")
    if result["files"] != 1:
        fail(f"应删除 1 个素材文件，实际 {result['files']}: {result}")
    if result["extracted_dirs"] != 1:
        fail(f"应删除 1 个抽取目录，实际 {result['extracted_dirs']}: {result}")
    if result["license_files"] != 1:
        fail(f"应删除 1 个孤儿授权文件，实际 {result['license_files']}: {result}")
    if old.exists() or (DATA_DIR / "extracted" / ("a" * 64)).exists():
        fail("过期素材文件或抽取目录仍在")
    if orphan_license.exists():
        fail("孤儿授权文件未被清理")
    if not fresh.exists():
        fail("未过期素材被误删")
    if not kept_license.exists():
        fail("被 LicenseRecord 引用的授权文件被误删")

    with Session(engine) as session:
        assets = session.exec(select(Asset)).all()
        if [item.sha256 for item in assets] != ["b" * 64]:
            fail(f"过期素材记录未删除: {[item.sha256 for item in assets]}")
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
            rows = session.exec(select(model)).all()
            if rows:
                fail(f"{model.__name__} 仍有 {len(rows)} 条残留")
        if not session.exec(select(LicenseRecord)).all():
            fail("授权记录不应被清理（只清孤儿文件）")

    for name in ("Dockerfile", "docker-compose.yml", ".dockerignore", "app/workers/run.py"):
        if not (ROOT / name).exists():
            fail(f"缺少部署产物：{name}")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    if "worker:" not in compose or "WORKER_IN_APP" not in compose:
        fail("docker-compose.yml 未拆分 worker 或未关闭 app 内 worker")

    print("[OK] Phase 7 验收通过")
    print(
        f"     清理: 素材 {result['assets']} / 文件 {result['files']}"
        f" / 抽取目录 {result['extracted_dirs']} / 孤儿授权 {result['license_files']}"
    )
    print("     部署产物: Dockerfile / docker-compose.yml / .dockerignore / app.workers.run")


if __name__ == "__main__":
    main()
