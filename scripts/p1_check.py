"""Phase 1 验收（等价于 `make p1`）。

启动临时 uvicorn（独立 SQLite + 独立 DATA_DIR），上传 15 秒示例视频，断言：
Job 完成、素材时长/分辨率正确、抽取产物含关键帧图片、OCR 文本、ASR 转写 JSON。
本机未安装 make 时直接运行：python scripts/p1_check.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
VIDEO = ROOT / "fixtures" / "media" / "sample_15s.mp4"
DATA_DIR = ROOT / "data" / "p1_check_data"
DB_PATH = DATA_DIR / "p1_check.db"
DEADLINE_SECONDS = 300.0  # 首次运行可能要下载 whisper 模型
TEXT_PATTERN = re.compile(r"SALE|NIKE|ARRIVAL|OFF|限时|优惠|立即", re.IGNORECASE)


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


def ensure_fixture() -> None:
    if VIDEO.exists():
        return
    print("生成示例视频（一次性）...")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "make_fixture_video.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not VIDEO.exists():
        fail(f"生成示例视频失败: {result.stdout[-500:]} {result.stderr[-500:]}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ensure_fixture()

    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{DB_PATH.as_posix()}"
    env["DATA_DIR"] = str(DATA_DIR)
    env["APP_HMAC_SECRET"] = "p1-check-secret"
    env["EXTRACT_ENABLED"] = "true"
    env["PYTHONUTF8"] = "1"  # Windows 控制台默认 GBK，日志里的中文会乱码

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
            response = client.post(
                f"{base_url}/api/upload",
                files={"file": (VIDEO.name, VIDEO.read_bytes(), "video/mp4")},
            )
            if response.status_code != 200:
                fail(f"上传失败 {response.status_code}: {response.text[:300]}")
            job_id = response.json()["job_id"]

            data = None
            while time.perf_counter() - started < DEADLINE_SECONDS:
                data = client.get(f"{base_url}/api/jobs/{job_id}").json()
                if data["job"]["status"] in ("done", "failed"):
                    break
                time.sleep(0.5)
            elapsed = time.perf_counter() - started

            if data is None or data["job"]["status"] != "done":
                job = (data or {}).get("job") or {}
                fail(
                    "任务未完成: "
                    f"status={job.get('status')} stage={job.get('stage')} "
                    f"error={job.get('error')}"
                )

            asset = data["asset"]
            duration = asset["duration"] or 0
            if not 14.0 <= duration <= 16.0:
                fail(f"素材时长异常: {duration}")
            if (asset["width"], asset["height"]) != (1280, 720):
                fail(f"素材分辨率异常: {asset['width']}x{asset['height']}")

            if not data.get("extraction_available"):
                fail("/api/jobs 未标记抽取产物可用")

            extraction = client.get(f"{base_url}/api/jobs/{job_id}/extraction")
            if extraction.status_code != 200:
                fail(f"抽取结果接口异常: {extraction.status_code}")
            result = extraction.json()

            frames = result.get("frames") or []
            if not 1 <= len(frames) <= 12:
                fail(f"关键帧数量不在 1..12: {len(frames)}")
            for frame in frames:
                frame_path = DATA_DIR / "extracted" / asset["sha256"] / "frames" / frame["path"]
                if not frame_path.exists():
                    fail(f"关键帧文件缺失: {frame_path}")
                if frame["timestamp"] is None or not 0 <= frame["timestamp"] <= duration:
                    fail(f"关键帧时间戳异常: {frame}")

            ocr_text = (result.get("ocr") or {}).get("text") or ""
            if not ocr_text.strip():
                fail("OCR 文本为空")
            if not TEXT_PATTERN.search(ocr_text):
                fail(f"OCR 未识别出预期文字: {ocr_text[:200]!r}")

            asr = result.get("asr") or {}
            if asr.get("status") != "ok":
                fail(f"ASR 状态异常: {asr.get('status')} / {asr.get('error')}")
            if not (asr.get("text") or "").strip():
                fail("ASR 转写为空")
            if not asr.get("segments"):
                fail("ASR 缺少分段")
            if asr["segments"][-1]["end"] > duration + 1.0:
                fail(f"ASR 分段越界: {asr['segments'][-1]}")

            print("[OK] Phase 1 验收通过")
            print(f"     上传到完成耗时: {elapsed:.2f}s（含模型首次加载，未计 SLA）")
            print(f"     分镜数: {result['media'].get('scene_count')}，关键帧: {len(frames)}")
            print(f"     OCR({result['ocr'].get('engine')}): {ocr_text[:120]!r}")
            print(f"     ASR({asr.get('model')}/{asr.get('language')}): {asr.get('text')[:160]!r}")
            print("     extraction.json 摘要:")
            summary = {
                key: result.get(key)
                for key in ("schema", "elapsed_ms", "warnings")
            }
            summary["frames"] = [
                {k: frame[k] for k in ("index", "timestamp", "scene_index", "path", "phash")}
                for frame in frames
            ]
            print(json.dumps(summary, ensure_ascii=False, indent=2)[:2000])
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    main()
