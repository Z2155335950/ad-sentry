"""Phase 0 验收（等价于 `make p0`）。

启动临时 uvicorn（独立 SQLite），上传 fixtures/media/sample_image.png，
断言 5 秒内产出 fixture 卡片、复核页可打开、无效签名被拒。
本机未安装 make 时直接运行：python scripts/p0_check.py
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
IMAGE = ROOT / "fixtures" / "media" / "sample_image.png"
DEADLINE_SECONDS = 5.0


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def fail(message: str) -> None:
    print(f"[FAIL] {message}")
    sys.exit(1)


def wait_ready(base_url: str, process: subprocess.Popen, timeout: float = 25.0) -> None:
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


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if not IMAGE.exists():
        fail(f"缺少测试图 {IMAGE}，先运行 python scripts/make_fixture_image.py")

    db_path = ROOT / "data" / "p0_check.db"
    for suffix in ("", "-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)

    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    env["APP_HMAC_SECRET"] = "p0-check-secret"

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
        started = time.perf_counter()
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                f"{base_url}/api/upload",
                files={"file": ("sample_image.png", IMAGE.read_bytes(), "image/png")},
            )
            if response.status_code != 200:
                fail(f"上传失败 {response.status_code}: {response.text[:300]}")
            job_id = response.json()["job_id"]

            data = None
            while time.perf_counter() - started < DEADLINE_SECONDS:
                data = client.get(f"{base_url}/api/jobs/{job_id}").json()
                if data["job"]["status"] in ("done", "failed"):
                    break
                time.sleep(0.1)
            elapsed = time.perf_counter() - started

            if data is None or data["job"]["status"] != "done":
                fail(f"任务未在 {DEADLINE_SECONDS}s 内完成: {data and data['job']}")
            if elapsed > DEADLINE_SECONDS:
                fail(f"耗时 {elapsed:.2f}s，超过 {DEADLINE_SECONDS}s")

            findings = data["findings"]
            card = data["card"]
            if not findings:
                fail("fixture 卡片为空")
            if card is None:
                fail("未生成卡片 JSON")

            title = card["header"]["title"]["content"]
            if "P1" not in title:
                fail(f"卡片标题未包含 P1: {title}")

            notes = [el for el in card["elements"] if el.get("tag") == "note"]
            if not notes or "不构成法律意见" not in notes[0]["elements"][0]["content"]:
                fail("卡片缺少免责声明")

            actions = [el for el in card["elements"] if el.get("tag") == "action"]
            review_url = actions[0]["actions"][0]["url"] if actions else ""
            if "/review/" not in review_url:
                fail("卡片缺少复核页按钮")

            page = client.get(review_url)
            if page.status_code != 200:
                fail(f"复核页打不开: {page.status_code}")
            if "Nike" not in page.text:
                fail("复核页缺少证据内容")

            bad = client.get(review_url.replace("token=", "token=bad", 1))
            if bad.status_code != 403:
                fail(f"无效签名未返回 403: {bad.status_code}")

        print("[OK] Phase 0 验收通过")
        print(f"     上传到卡片完成耗时: {elapsed:.2f}s（上限 {DEADLINE_SECONDS}s）")
        print(f"     信号数: {len(findings)}，卡片标题: {title}")
        print("     卡片 JSON:")
        print(json.dumps(card, ensure_ascii=False, indent=2)[:1600])
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    main()
