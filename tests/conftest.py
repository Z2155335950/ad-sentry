"""测试环境：独立 SQLite 与独立 DATA_DIR，避免污染开发库。必须在导入 app 之前设置环境变量。"""

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_DB = ROOT / "data" / "test_adsentry.db"
TEST_DATA = ROOT / "data" / "test_data"

for suffix in ("", "-wal", "-shm"):
    Path(str(TEST_DB) + suffix).unlink(missing_ok=True)
shutil.rmtree(TEST_DATA, ignore_errors=True)
TEST_DATA.mkdir(parents=True, exist_ok=True)

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["DATA_DIR"] = str(TEST_DATA)
os.environ["APP_HMAC_SECRET"] = "test-secret"
os.environ["FIXTURES_DIR"] = str(ROOT / "fixtures")
os.environ["WATCHLISTS_DIR"] = str(ROOT / "data" / "watchlists")
# 单测不跑真实 OCR/ASR/ffmpeg；抽取层有独立单测与 p1 端到端验收
os.environ["EXTRACT_ENABLED"] = "false"
# 骨架测试依赖 fixtures/fake_findings.json；工具层有独立单测与 p2 端到端验收
os.environ["TOOLS_ENABLED"] = "false"
os.environ["TOOLS_MODE"] = "mock"
# 屏蔽本机 .env 里的真实凭证，保证单测结果与开发机状态无关
for _key in (
    "ANTHROPIC_API_KEY",
    "GOOGLE_VISION_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "ACRCLOUD_HOST",
    "ACRCLOUD_ACCESS_KEY",
    "ACRCLOUD_ACCESS_SECRET",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_ENCRYPT_KEY",
    "FEISHU_BITABLE_APP_TOKEN",
    "FEISHU_BITABLE_TABLE_ID",
):
    os.environ[_key] = ""
# 飞书入口只验证事件处理与 mock outbox，不连真实开放平台
os.environ["FEISHU_ENABLED"] = "true"
os.environ["FEISHU_MODE"] = "mock"
os.environ["FEISHU_VERIFICATION_TOKEN"] = "test-token"
Path(TEST_DATA / "feishu_outbox.jsonl").unlink(missing_ok=True)


def sample_image_bytes() -> bytes:
    return (ROOT / "fixtures" / "media" / "sample_image.png").read_bytes()
