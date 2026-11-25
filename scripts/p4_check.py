"""Phase 4 验收（等价于 `make p4`）。

断言：
1) 三份授权书字段抽取正确，状态分别为 已过期 / 地域不符 / 有效；
2) 未登记投放信息时标注「无法核验」，登记 Campaign 后才给出地域结论；
3) 授权挂接素材后重算状态并写库；
4) 复核动作（确认/驳回）落库，驳回空理由被拒。

本机未安装 make 时直接运行：python scripts/p4_check.py
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
MEDIA = ROOT / "fixtures" / "media" / "sample_image.png"
LIC_DIR = ROOT / "fixtures" / "licenses"
EXPIRED = "license_expired.pdf"
SEA_EXCLUDED = "license_sea_excluded.pdf"
VALID = "license_valid.pdf"
DATA_DIR = ROOT / "data" / "p4_check_data"
DB_PATH = DATA_DIR / "p4_check.db"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def fail(message: str) -> None:
    print(f"[FAIL] {message}")
    sys.exit(1)


def ensure_fixtures() -> None:
    missing = [name for name in (EXPIRED, SEA_EXCLUDED, VALID) if not (LIC_DIR / name).exists()]
    if not missing:
        return
    print("生成 Phase 4 合成授权书...")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "make_p4_fixtures.py"), "--force"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fail(f"生成授权书失败: {(result.stderr or result.stdout)[-500:]}")


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


def upload_asset(client: httpx.Client, base_url: str) -> dict:
    response = client.post(
        f"{base_url}/api/upload",
        files={"file": (MEDIA.name, MEDIA.read_bytes(), "image/png")},
    )
    if response.status_code != 200:
        fail(f"素材上传失败 {response.status_code}: {response.text[:300]}")
    job_id = response.json()["job_id"]
    deadline = time.time() + 30.0
    data = {}
    while time.time() < deadline:
        data = client.get(f"{base_url}/api/jobs/{job_id}").json()
        if data["job"]["status"] in ("done", "failed"):
            break
        time.sleep(0.2)
    if data.get("job", {}).get("status") != "done":
        fail(f"素材任务未完成: {data.get('job')}")
    return data


def upload_license(client: httpx.Client, base_url: str, name: str) -> dict:
    path = LIC_DIR / name
    response = client.post(
        f"{base_url}/api/licenses/upload",
        files={"file": (name, path.read_bytes(), "application/pdf")},
    )
    if response.status_code != 200:
        fail(f"授权书上传失败 {name} {response.status_code}: {response.text[:300]}")
    return response.json()


def token_from_card(card: dict | None) -> str:
    for element in (card or {}).get("elements", []):
        for action in element.get("actions") or []:
            query = parse_qs(urlparse(action.get("url") or "").query)
            if "token" in query:
                return query["token"][0]
    fail("卡片里没有找到复核 token")
    raise AssertionError


def status_by_id(ledger: dict) -> dict[int, str]:
    return {item["record"]["id"]: item["status"] for item in ledger["licenses"]}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ensure_fixtures()
    if not MEDIA.exists():
        fail(f"缺少素材 {MEDIA}")
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{DB_PATH.as_posix()}"
    env["DATA_DIR"] = str(DATA_DIR)
    env["APP_HMAC_SECRET"] = "p4-check-secret"
    env["EXTRACT_ENABLED"] = "false"  # 只验证台账链路，抽取层由 p1 验收
    env["TOOLS_ENABLED"] = "false"    # 用 fixture 信号，避免外部工具
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
        started = time.perf_counter()
        with httpx.Client(timeout=30.0) as client:
            job = upload_asset(client, base_url)
            asset_id = job["asset"]["id"]
            findings = job["findings"]
            if not findings:
                fail("fixture 未产出风险信号，无法验证复核动作")

            expired = upload_license(client, base_url, EXPIRED)
            sea = upload_license(client, base_url, SEA_EXCLUDED)
            valid = upload_license(client, base_url, VALID)

            for payload, name in ((expired, EXPIRED), (sea, SEA_EXCLUDED), (valid, VALID)):
                if payload["missing_fields"]:
                    fail(f"{name} 关键字段缺失: {payload['missing_fields']}")
                if payload["record"]["territory"] is None:
                    fail(f"{name} 未解析出地域范围")

            if expired["record"]["status"] != "expired":
                fail(f"过期授权书状态应为 expired，实际 {expired['record']['status']}")
            for payload, name in ((sea, SEA_EXCLUDED), (valid, VALID)):
                if payload["record"]["status"] != "effective":
                    fail(f"{name} 未登记投放前应为 effective，实际 {payload['record']['status']}")

            before = client.get(f"{base_url}/api/assets/{asset_id}/ledger").json()
            if not before["campaign_missing"]:
                fail("未登记 Campaign 时应标注 campaign_missing")

            campaign = client.post(
                f"{base_url}/api/assets/{asset_id}/campaign",
                json={
                    "platform": "tiktok",
                    "market": "SEA",
                    "start": "2026-12-01",
                    "end": "2027-01-31",
                },
            )
            if campaign.status_code != 200:
                fail(f"登记投放信息失败 {campaign.status_code}: {campaign.text[:300]}")

            for payload in (expired, sea, valid):
                linked = client.post(
                    f"{base_url}/api/licenses/{payload['record']['id']}/link",
                    json={"asset_id": asset_id},
                )
                if linked.status_code != 200:
                    fail(f"挂接授权失败 {linked.status_code}: {linked.text[:300]}")

            ledger = client.get(f"{base_url}/api/assets/{asset_id}/ledger").json()
            if ledger["campaign_missing"]:
                fail("登记 Campaign 后仍标注 campaign_missing")
            statuses = status_by_id(ledger)
            expected = {
                expired["record"]["id"]: "expired",
                sea["record"]["id"]: "out_of_territory",
                valid["record"]["id"]: "effective",
            }
            for license_id, want in expected.items():
                got = statuses.get(license_id)
                if got != want:
                    fail(f"授权 #{license_id} 状态应为 {want}，实际 {got}")

            finding_id = findings[0]["id"]
            token = token_from_card(job.get("card"))
            rejected = client.post(
                f"{base_url}/api/review/{finding_id}/action?token={token}",
                json={"action": "reject", "reason": "  "},
            )
            if rejected.status_code != 400:
                fail(f"驳回空理由应返回 400，实际 {rejected.status_code}")
            confirmed = client.post(
                f"{base_url}/api/review/{finding_id}/action?token={token}",
                json={"action": "confirm", "reason": "演示复核"},
            )
            if confirmed.status_code != 200:
                fail(f"复核动作失败 {confirmed.status_code}: {confirmed.text[:300]}")

        elapsed = time.perf_counter() - started
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute(
            "SELECT finding_id, action, reason FROM reviewaction ORDER BY id"
        ).fetchall()
    if len(rows) != 1 or rows[0][1] != "confirm":
        fail(f"复核动作未正确落库: {rows}")

    print("[OK] Phase 4 验收通过")
    print(f"     台账链路耗时: {elapsed:.2f}s")
    print(
        "     授权状态: "
        + json.dumps(
            {"expired": "expired", "sea_excluded": "out_of_territory", "valid": "effective"},
            ensure_ascii=False,
        )
    )
    print(f"     复核动作落库: finding={rows[0][0]} action={rows[0][1]} reason={rows[0][2]}")


if __name__ == "__main__":
    main()
