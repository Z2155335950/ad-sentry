"""Phase 7 演示：五个场景离线回放并打印现场 URL（等价于 `make demo`）。

不启动外部服务：用 TestClient 跑真实路由，结果写入独立演示库；
打印的 URL 用 PUBLIC_BASE_URL（默认 http://127.0.0.1:8000），供现场服务使用。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "demo_check_data"
DB_PATH = DATA_DIR / "demo_check.db"

os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH.as_posix()}"
os.environ["DATA_DIR"] = str(DATA_DIR)
os.environ["APP_HMAC_SECRET"] = "demo-check-secret"
os.environ["DEMO_MODE"] = "true"
os.environ["EXTRACT_ENABLED"] = "false"
os.environ["TOOLS_ENABLED"] = "false"
os.environ["FEISHU_ENABLED"] = "false"
os.environ["WORKER_IN_APP"] = "false"

from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

SCENARIOS = [
    ("risk_video", "高风险视频全链路"),
    ("license_expiry", "证据链与授权到期"),
    ("recheck_pass", "替换后重检通过"),
    ("clean_ai", "干净 AI 人像素材"),
    ("batch_scan", "历史素材批量巡检"),
]


def fail(message: str) -> None:
    print(f"[FAIL] {message}")
    sys.exit(1)


def check_risk_video(client: TestClient) -> dict:
    data = client.get("/demo/replay/risk_video?format=json").json()
    levels = {item["level"] for item in data["findings"]}
    if levels != {"P0", "P1", "P2"}:
        fail(f"risk_video 等级不符: {levels}")
    if "P0" not in data["card"]["header"]["title"]["content"]:
        fail("risk_video 卡片标题未体现 P0")
    if not data["review_urls"]:
        fail("risk_video 缺少复核链接")
    return data


def check_license_expiry(client: TestClient) -> dict:
    data = client.get("/demo/replay/license_expiry?format=json").json()
    bgm = next((f for f in data["findings"] if f["category"] == "bgm"), None)
    if bgm is None:
        fail("license_expiry 缺少 BGM 信号")
    if bgm["level"] != "P1":
        fail(f"license_expiry 的 BGM 应因授权过期抬到 P1，实际 {bgm['level']}")
    if not any(ev["kind"] == "license" for ev in bgm["evidence"]):
        fail("license_expiry 的 BGM 缺少 license 证据")
    note = " ".join(ev.get("note") or "" for ev in bgm["evidence"])
    if "过期" not in note:
        fail(f"license_expiry 未给出过期原因: {note}")
    return data


def check_recheck_pass(client: TestClient) -> dict:
    data = client.get("/demo/replay/recheck_pass?format=json").json()
    if data["findings"]:
        fail(f"recheck_pass 应无信号，实际 {len(data['findings'])} 条")
    return data


def check_clean_ai(client: TestClient) -> dict:
    data = client.get("/demo/replay/clean_ai?format=json").json()
    levels = {item["level"] for item in data["findings"]}
    if levels - {"P2", "P3"}:
        fail(f"clean_ai 出现 P0/P1: {levels}")
    if "P2" not in levels:
        fail("clean_ai 缺少 AI 疑似 P2")
    return data


def check_batch(client: TestClient) -> dict:
    data = client.get("/demo/replay/batch_scan?format=json").json()["batch"]
    expected = {"P0": 2, "P1": 2, "P2": 2, "P3": 1}
    if data["totals"] != expected:
        fail(f"batch_scan 汇总不符: {data['totals']} != {expected}")
    if data["assets"] != 4:
        fail(f"batch_scan 素材数应为 4，实际 {data['assets']}")
    return data


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with TestClient(app) as client:
        if client.get("/demo").status_code != 200:
            fail("/demo 页面打不开")
        check_risk_video(client)
        check_license_expiry(client)
        check_recheck_pass(client)
        check_clean_ai(client)
        batch = check_batch(client)

    base = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    print("[OK] Phase 7 五个演示场景离线回放通过")
    print(
        f"     批量汇总: 4 条素材 / {batch['findings']} 条信号"
        f" / 需处理 {batch['needs_action']} 条"
    )
    print("     现场 URL（服务以 DEMO_MODE=true 启动后可用）：")
    for index, (scenario_id, title) in enumerate(SCENARIOS, start=1):
        print(f"       {index}) {title:<12} {base}/demo/replay/{scenario_id}")


if __name__ == "__main__":
    main()
