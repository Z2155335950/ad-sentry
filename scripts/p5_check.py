"""Phase 5 验收（等价于 `make p5`）。

mock 回环：飞书事件 → 回执 → 建任务 → worker 跑完 → 推送卡片 → 复核动作落库。
不连接真实开放平台；真实长连接需要 FEISHU_APP_ID / SECRET（见 PROGRESS 已知限制）。

本机未安装 make 时直接运行：python scripts/p5_check.py
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
from urllib.parse import parse_qs, urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "p5_check_data"
DB_PATH = DATA_DIR / "p5_check.db"
TOKEN = "p5-check-token"

EVENT_TEMPLATE = {
    "schema": "2.0",
    "header": {
        "event_id": "evt_1",
        "event_type": "im.message.receive_v1",
        "token": TOKEN,
    },
    "event": {
        "sender": {"sender_id": {"open_id": "ou_test"}},
        "message": {
            "message_id": "om_test_1",
            "chat_id": "oc_test_1",
            "message_type": "image",
            "content": json.dumps({"image_key": "img_test_1"}),
        },
    },
}


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


def read_outbox() -> list[dict]:
    path = DATA_DIR / "feishu_outbox.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update(
        DATABASE_URL=f"sqlite:///{DB_PATH.as_posix()}",
        DATA_DIR=str(DATA_DIR),
        APP_HMAC_SECRET="p5-check-secret",
        PUBLIC_BASE_URL=base_url,
        EXTRACT_ENABLED="false",
        TOOLS_ENABLED="false",
        FEISHU_ENABLED="true",
        FEISHU_MODE="mock",
        FEISHU_VERIFICATION_TOKEN=TOKEN,
        FEISHU_ENCRYPT_KEY="",
        FEISHU_APP_ID="",
        FEISHU_APP_SECRET="",
        PYTHONUTF8="1",
    )

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
        with httpx.Client(timeout=30.0) as client:
            challenge = client.post(
                f"{base_url}/api/feishu/event",
                json={"type": "url_verification", "token": TOKEN, "challenge": "abc123"},
            )
            if challenge.status_code != 200 or challenge.json().get("challenge") != "abc123":
                fail(f"url_verification 失败: {challenge.status_code} {challenge.text[:200]}")

            first = client.post(f"{base_url}/api/feishu/event", json=EVENT_TEMPLATE)
            if first.status_code != 200:
                fail(f"消息事件处理失败: {first.status_code} {first.text[:300]}")
            result = first.json()
            job_id = result.get("job_id")
            if not job_id:
                fail(f"事件未创建任务: {result}")

            duplicate = client.post(f"{base_url}/api/feishu/event", json=EVENT_TEMPLATE).json()
            if not duplicate.get("duplicate") or duplicate.get("job_id") != job_id:
                fail(f"重复事件未按 message_id 去重: {duplicate}")

            deadline = time.time() + 30
            data = {}
            while time.time() < deadline:
                data = client.get(f"{base_url}/api/jobs/{job_id}").json()
                if data["job"]["status"] in ("done", "failed"):
                    break
                time.sleep(0.2)
            if data.get("job", {}).get("status") != "done":
                fail(f"任务未完成: {data.get('job')}")

            outbox = read_outbox()
            texts = [e for e in outbox if e.get("msg_type") == "text"]
            cards = [e for e in outbox if e.get("msg_type") == "interactive"]
            if not texts or "已接收" not in texts[0]["content"]["text"]:
                fail(f"未发送接收回执: {[t.get('content') for t in texts]}")
            if not cards:
                fail("任务完成后未推送卡片")
            card = cards[0]["content"]
            title = card["header"]["title"]["content"]
            if "P1" not in title:
                fail(f"卡片标题未体现最高等级: {title}")
            action = next(el for el in card["elements"] if el.get("tag") == "action")
            review_url = action["actions"][0]["url"]
            parsed = urlparse(review_url)
            token = parse_qs(parsed.query).get("token", [""])[0]
            if not token:
                fail("卡片复核链接缺少签名 token")

            page = client.get(f"{base_url}{parsed.path}?{parsed.query}")
            if page.status_code != 200:
                fail(f"复核页打不开: {page.status_code}")

            finding_id = data["findings"][0]["id"]
            rejected = client.post(
                f"{base_url}/api/review/{finding_id}/action?token={token}",
                json={"action": "reject", "reason": " "},
            )
            if rejected.status_code != 400:
                fail(f"驳回空理由应返回 400，实际 {rejected.status_code}")
            confirmed = client.post(
                f"{base_url}/api/review/{finding_id}/action?token={token}",
                json={"action": "confirm", "reason": "飞书回环验证"},
            )
            if confirmed.status_code != 200:
                fail(f"复核动作失败: {confirmed.text[:300]}")
        elapsed = time.perf_counter() - started
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

    with sqlite3.connect(DB_PATH) as connection:
        events = connection.execute(
            "SELECT message_id, chat_id, job_id, status FROM feishuevent"
        ).fetchall()
        actions = connection.execute("SELECT action FROM reviewaction").fetchall()
    if len(events) != 1 or events[0][3] != "processed":
        fail(f"飞书事件状态不正确: {events}")
    if [row[0] for row in actions] != ["confirm"]:
        fail(f"复核动作未正确落库: {actions}")

    print("[OK] Phase 5 验收通过")
    print(f"     飞书回环耗时: {elapsed:.2f}s")
    print(f"     卡片标题: {card['header']['title']['content']}")
    print(f"     事件: {events[0][:3]} status={events[0][3]}")
    print(f"     复核链接: {review_url}")


if __name__ == "__main__":
    main()
