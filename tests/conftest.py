"""测试环境：独立 SQLite，避免污染开发库。必须在导入 app 之前设置环境变量。"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_DB = ROOT / "data" / "test_adsentry.db"

for suffix in ("", "-wal", "-shm"):
    Path(str(TEST_DB) + suffix).unlink(missing_ok=True)

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["APP_HMAC_SECRET"] = "test-secret"
os.environ["FIXTURES_DIR"] = str(ROOT / "fixtures")
# 单测不跑真实 OCR/ASR/ffmpeg；抽取层有独立单测与 p1 端到端验收
os.environ["EXTRACT_ENABLED"] = "false"


def sample_image_bytes() -> bytes:
    return (ROOT / "fixtures" / "media" / "sample_image.png").read_bytes()
