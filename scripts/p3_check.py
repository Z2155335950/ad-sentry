"""Phase 3 验收（等价于 `make p3`）。

1) 三个模块的本地能力：Nike 命中、bike 不误报、政策条款带生效日期；
2) 端到端：上传 `fixtures/media/sample_15s.mp4`（mock 工具层），断言
   - logo 信号 P0：Vision/CLIP/OCR 多路证据 + 注册号 + bbox；
   - 商标词信号 P1：OCR 文本命中「NIKE」并附注册号；
   - 卡片无「侵权」措辞、含免责声明；
3) 干净素材 `clean_ai.png`：无 logo / 商标词信号、无 P0/P1。

本机未安装 make 时直接运行：python scripts/p3_check.py
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
VIDEO = ROOT / "fixtures" / "media" / "sample_15s.mp4"
CLEAN_IMAGE = ROOT / "fixtures" / "media" / "clean_ai.png"
CACHE = ROOT / "fixtures" / "cache"
DATA_DIR = ROOT / "data" / "p3_check_data"
DB_PATH = DATA_DIR / "p3_check.db"
DEADLINE_SECONDS = 300.0
NIKE_REG_NO = "6124779"
ANCHOR_KINDS = {"reference", "clause", "license"}


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def fail(message: str) -> None:
    print(f"[FAIL] {message}")
    sys.exit(1)


def ensure_fixtures() -> None:
    import hashlib

    if not VIDEO.exists():
        fail(f"缺少 {VIDEO}，先运行 scripts/make_fixture_video.py")
    expected = hashlib.sha256(VIDEO.read_bytes()).hexdigest()
    path = CACHE / "logo.json"
    payload = {}
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
    if expected not in (payload.get("assets") or {}):
        print("生成 Phase 3 logo mock（一次性）...")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "make_p3_fixtures.py"), "--force"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            fail(f"生成 mock 失败: {(result.stderr or result.stdout)[-500:]}")


def check_modules() -> None:
    sys.path.insert(0, str(ROOT))
    from app.tools import policy, trademark

    nike = trademark.match_text("Big NIKE® sale, bike-friendly shoes")
    marks = {match.brand.mark for match in nike}
    if "Nike" not in marks:
        fail(f"Nike 未命中: {marks}")
    if any(match.matched_text.casefold() == "bike" for match in nike):
        fail("bike 被误匹配为 Nike")

    only_bike = trademark.match_text("bike-friendly city tour")
    if any(match.brand.mark == "Nike" for match in only_bike):
        fail("仅含 bike 的文本命中了 Nike")

    chinese = trademark.match_text("耐克新款跑鞋")
    if not any(match.brand.mark == "Nike" for match in chinese):
        fail("中文别名「耐克」未命中")

    generic = trademark.match_text("PLUS PRO ULTRA")
    if not generic or any(signal["level"] != "P3" for signal in _generic_signals(generic)):
        fail("通用词未按 P3 处理")

    clauses = policy.search("AI 标注义务")
    if not clauses:
        fail("政策检索「AI 标注义务」无结果")
    top = clauses[0]
    for field in ("id", "text", "effective_date", "url"):
        if not top.get(field):
            fail(f"政策条款缺少 {field}: {top}")
    if "AI" not in top["text"]:
        fail(f"政策条款与查询不相关: {top['id']}")
    if policy.search("zzzz qqqq unrelated phrase"):
        fail("无相关条款时不应产出结果")

    print("     [模块] Nike 命中 / bike 无误报 / 中文别名命中 / 通用词 P3")
    print(f"     [模块] 政策 top1: {top['id']} · 生效 {top['effective_date']}")


def _generic_signals(matches) -> list[dict]:
    from app.tools import trademark

    signals = []
    for match in matches:
        signals.append(
            {
                "level": trademark.level_for_brand(match.brand),
                "mark": match.brand.mark,
            }
        )
    return signals


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


def _by_category(findings: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for finding in findings:
        grouped.setdefault(finding["category"], []).append(finding)
    return grouped


def check_risk_asset(data: dict) -> None:
    findings = data["findings"]
    if not findings:
        fail("植入素材未产出任何风险信号")
    for finding in findings:
        if "侵权" in finding["suggestion"]:
            fail(f"措辞出现「侵权」判定: {finding['suggestion']}")

    grouped = _by_category(findings)

    logo = (grouped.get("logo") or [None])[0]
    if logo is None:
        fail(f"缺少 logo 信号（实际 {sorted(grouped)}）")
    if logo["level"] != "P0":
        fail(f"logo 等级应为 P0，实际 {logo['level']}")
    evidence = logo["evidence"]
    if not any(ev["kind"] in ANCHOR_KINDS for ev in evidence):
        fail("logo 为 P0 但缺少参照物证据")
    if not any(NIKE_REG_NO in (ev.get("ref_record_id") or "") for ev in evidence):
        fail(f"logo 证据缺少注册号 {NIKE_REG_NO}")
    if not any(ev.get("bbox") for ev in evidence):
        fail("logo 缺少 bbox 证据")
    if not any(ev.get("t_start") is not None for ev in evidence):
        fail("logo 缺少时间戳证据")
    if "Vision" not in json.dumps(evidence, ensure_ascii=False):
        fail("logo 证据未体现 Vision 路线")

    text = (grouped.get("trademark_text") or [None])[0]
    if text is None:
        fail("缺少 trademark_text 信号（OCR 未命中 NIKE？）")
    if text["level"] != "P1":
        fail(f"trademark_text 等级应为 P1，实际 {text['level']}")
    if "Nike" not in text["suggestion"] and "NIKE" not in text["suggestion"]:
        fail(f"trademark_text 建议未提及 Nike: {text['suggestion']}")
    t_evidence = text["evidence"]
    if not any(NIKE_REG_NO in (ev.get("ref_record_id") or "") for ev in t_evidence):
        fail("trademark_text 证据缺少注册号")
    if not any(ev.get("ref_url") for ev in t_evidence):
        fail("trademark_text 缺少注册信息链接")
    if not any(ev.get("bbox") for ev in t_evidence):
        fail("trademark_text 缺少文字 bbox")

    card = data.get("card")
    if card is None:
        fail("未生成卡片 JSON")
    if "P0" not in card["header"]["title"]["content"]:
        fail(f"卡片标题未体现最高等级 P0: {card['header']['title']['content']}")
    notes = [el for el in card["elements"] if el.get("tag") == "note"]
    if not notes or "不构成法律意见" not in notes[0]["elements"][0]["content"]:
        fail("卡片缺少免责声明")


def check_clean_asset(data: dict) -> None:
    findings = data["findings"]
    bad = [f for f in findings if f["level"] in ("P0", "P1")]
    if bad:
        fail(f"干净素材出现 P0/P1 误报: {json.dumps(bad, ensure_ascii=False)[:300]}")
    grouped = _by_category(findings)
    for category in ("logo", "trademark_text"):
        if category in grouped:
            detail = json.dumps(grouped[category], ensure_ascii=False)[:200]
            fail(f"干净素材出现 {category} 误报: {detail}")


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
        fail(f"缺少干净素材 {CLEAN_IMAGE}")

    check_modules()

    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{DB_PATH.as_posix()}"
    env["DATA_DIR"] = str(DATA_DIR)
    env["APP_HMAC_SECRET"] = "p3-check-secret"
    env["EXTRACT_ENABLED"] = "true"
    env["ASR_ENABLED"] = "false"  # Phase 3 不需要转写；口播匹配有单测覆盖
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
    clean = None
    try:
        wait_ready(base_url, process)
        with httpx.Client(timeout=30.0) as client:
            risk = upload_and_wait(client, base_url, VIDEO, DEADLINE_SECONDS)
            check_risk_asset(risk)

            clean = upload_and_wait(client, base_url, CLEAN_IMAGE, 60.0)
            check_clean_asset(clean)

        print("[OK] Phase 3 验收通过")
        print(f"     植入视频处理耗时: {risk['elapsed']:.2f}s")
        print(
            "     命中信号: "
            + ", ".join(
                f"{f['category']}={f['level']}"
                for f in sorted(risk["findings"], key=lambda f: f["level"])
            )
        )
        print(f"     干净素材信号数: {len(clean['findings'])}（无 P0/P1 / 无 logo / 无商标词）")
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
