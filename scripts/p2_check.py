"""Phase 2 验收（等价于 `make p2`）。

启动临时 uvicorn（独立 SQLite + 独立 DATA_DIR，TOOLS_MODE=mock），上传
`fixtures/media/risk_ad_10s.mp4`，断言植入素材命中三类预期信号：
版权图像 P0（图库页面）、公众人物 P1、BGM P2；再上传干净 AI 人像，断言无 P0/P1、
无公众人物误报。本机未安装 make 时直接运行：python scripts/p2_check.py
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
VIDEO = ROOT / "fixtures" / "media" / "risk_ad_10s.mp4"
CLEAN_IMAGE = ROOT / "fixtures" / "media" / "clean_ai.png"
DATA_DIR = ROOT / "data" / "p2_check_data"
DB_PATH = DATA_DIR / "p2_check.db"
DEADLINE_SECONDS = 240.0

EXPECTED = {"copyright_image": "P0", "celebrity": "P1", "bgm": "P2"}
ANCHOR_KINDS = {"reference", "clause", "license"}


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def fail(message: str) -> None:
    print(f"[FAIL] {message}")
    sys.exit(1)


def wait_ready(base_url: str, process: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("服务进程提前退出，检查端口或依赖")
        try:
            if httpx.get(f"{base_url}/api/health", timeout=1.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("服务启动超时")


def ensure_fixtures() -> None:
    missing = not VIDEO.exists() or not (ROOT / "fixtures" / "cache" / "music.json").exists()
    if not missing:
        return
    print("生成 Phase 2 演示素材与 mock（一次性）...")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "make_p2_fixtures.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not VIDEO.exists():
        fail(f"生成素材失败: {result.stdout[-500:]} {result.stderr[-500:]}")


def upload_and_wait(client: httpx.Client, base_url: str, path: Path, timeout: float) -> dict:
    started = time.perf_counter()
    response = client.post(
        f"{base_url}/api/upload",
        files={"file": (path.name, path.read_bytes(), "application/octet-stream")},
    )
    if response.status_code != 200:
        fail(f"上传失败 {response.status_code}: {response.text[:300]}")
    job_id = response.json()["job_id"]
    data = None
    while time.perf_counter() - started < timeout:
        data = client.get(f"{base_url}/api/jobs/{job_id}").json()
        if data["job"]["status"] in ("done", "failed"):
            break
        time.sleep(0.5)
    if data is None or data["job"]["status"] != "done":
        job = (data or {}).get("job") or {}
        fail(
            "任务未完成: "
            f"status={job.get('status')} stage={job.get('stage')} error={job.get('error')}"
        )
    data["elapsed"] = time.perf_counter() - started
    return data


def check_risk_asset(data: dict) -> None:
    findings = data["findings"]
    if not findings:
        fail("植入素材未产出任何风险信号")

    by_category = {}
    for finding in findings:
        by_category.setdefault(finding["category"], []).append(finding)
        if "侵权" in finding["suggestion"]:
            fail(f"措辞出现「侵权」判定: {finding['suggestion']}")

    for category, expected_level in EXPECTED.items():
        if category not in by_category:
            fail(f"缺少预期信号类别: {category}（实际 {sorted(by_category)}）")
        finding = by_category[category][0]
        if finding["level"] != expected_level:
            fail(f"{category} 等级应为 {expected_level}，实际 {finding['level']}")
        evidence = finding["evidence"]
        if expected_level in ("P0", "P1"):
            if not any(ev["kind"] in ANCHOR_KINDS for ev in evidence):
                fail(f"{category} 为 {expected_level} 但缺少参照物证据")
        if category in ("copyright_image", "celebrity"):
            if not any(ev.get("ref_url") or ev.get("ref_record_id") for ev in evidence):
                fail(f"{category} 缺少参照物链接/记录号")
        if category == "celebrity" and not any(ev.get("bbox") for ev in evidence):
            fail("celebrity 缺少人脸 bbox 证据")
        if category == "bgm" and not any(ev.get("t_start") is not None for ev in evidence):
            fail("bgm 缺少时间区间证据")

    card = data.get("card")
    if card is None:
        fail("未生成卡片 JSON")
    title = card["header"]["title"]["content"]
    if "P0" not in title:
        fail(f"卡片标题未体现最高等级 P0: {title}")
    notes = [el for el in card["elements"] if el.get("tag") == "note"]
    if not notes or "不构成法律意见" not in notes[0]["elements"][0]["content"]:
        fail("卡片缺少免责声明")


def check_clean_asset(data: dict) -> None:
    findings = data["findings"]
    bad = [f for f in findings if f["level"] in ("P0", "P1")]
    if bad:
        fail(f"干净素材出现 P0/P1 误报: {json.dumps(bad, ensure_ascii=False)[:300]}")
    if any(f["category"] == "celebrity" for f in findings):
        fail("干净 AI 人像被误报为公众人物")


def check_tool_calls() -> dict:
    if not DB_PATH.exists():
        fail("验收数据库不存在")
    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute(
            "SELECT tool, COUNT(*), SUM(cost_usd) FROM toolcall GROUP BY tool"
        ).fetchall()
    if not rows:
        fail("未记录任何 ToolCall")
    return {tool: {"count": count, "cost_usd": cost or 0.0} for tool, count, cost in rows}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ensure_fixtures()
    if not CLEAN_IMAGE.exists():
        fail(f"缺少干净素材 {CLEAN_IMAGE}（fixtures/README.md 有来源说明）")

    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{DB_PATH.as_posix()}"
    env["DATA_DIR"] = str(DATA_DIR)
    env["APP_HMAC_SECRET"] = "p2-check-secret"
    env["EXTRACT_ENABLED"] = "true"
    env["ASR_ENABLED"] = "false"  # Phase 2 不需要转写，避免首次加载 whisper
    env["TOOLS_ENABLED"] = "true"
    env["TOOLS_MODE"] = "mock"
    env["PYTHONUTF8"] = "1"

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
    )
    try:
        wait_ready(base_url, process)
        with httpx.Client(timeout=30.0) as client:
            risk = upload_and_wait(client, base_url, VIDEO, DEADLINE_SECONDS)
            check_risk_asset(risk)

            clean = upload_and_wait(client, base_url, CLEAN_IMAGE, 60.0)
            check_clean_asset(clean)

        print("[OK] Phase 2 验收通过")
        print(f"     植入视频处理耗时: {risk['elapsed']:.2f}s")
        print(
            "     命中信号: "
            + ", ".join(
                f"{f['category']}={f['level']}"
                for f in sorted(risk["findings"], key=lambda f: f["level"])
            )
        )
        print(f"     干净素材信号数: {len(clean['findings'])}（无 P0/P1）")
        print(f"     卡片标题: {risk['card']['header']['title']['content']}")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

    try:
        calls = check_tool_calls()
    except Exception as exc:  # 记账检查失败不掩盖前面的断言结果
        fail(f"ToolCall 记账检查失败: {exc}")
    print("     ToolCall 汇总: " + json.dumps(calls, ensure_ascii=False))


if __name__ == "__main__":
    main()
