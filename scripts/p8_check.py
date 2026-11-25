"""Phase 8 验收（等价于 `make p8` / `make verify-8`）。

三件事：
1. 真实上传链路延迟：图片 5 次、15s 视频 3 次，断言 p50 达到 PROMPT §7 的目标；
2. 干净集误报：跑 `app.eval.run_eval`，完整链路干净集 P0/P1 误报必须为 0；
3. 彩排：五个演示场景离线回放，并打印现场顺序与预期。

实时凭证与 Docker 只做就绪检查：缺失时标记 BLOCKED，不判失败（本机没有这些环境）。
"""

from __future__ import annotations

import json
import math
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "p8_check_data"
DB_PATH = DATA_DIR / "p8_check.db"
IMAGE = ROOT / "fixtures" / "media" / "sample_image.png"
VIDEO = ROOT / "fixtures" / "media" / "sample_15s.mp4"

IMAGE_RUNS = 5
VIDEO_RUNS = 3
IMAGE_P50_LIMIT = 20.0
VIDEO_P50_LIMIT = 60.0

os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH.as_posix()}"
os.environ["DATA_DIR"] = str(DATA_DIR)
os.environ["APP_HMAC_SECRET"] = "p8-check-secret"
os.environ["EXTRACT_ENABLED"] = "true"
os.environ["ASR_ENABLED"] = "true"
os.environ["TOOLS_ENABLED"] = "true"
os.environ["TOOLS_MODE"] = "mock"
os.environ["DEMO_MODE"] = "false"
os.environ["FEISHU_ENABLED"] = "false"
os.environ["WORKER_IN_APP"] = "true"


def fail(message: str) -> None:
    print(f"[FAIL] {message}")
    sys.exit(1)


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * ratio
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[int(position)]
    return ordered[low] * (high - position) + ordered[high] * (position - low)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


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


def upload_and_wait(
    client: httpx.Client, base_url: str, path: Path, timeout: float
) -> tuple[float, dict]:
    started = time.perf_counter()
    response = client.post(
        f"{base_url}/api/upload",
        files={"file": (path.name, path.read_bytes(), "application/octet-stream")},
    )
    if response.status_code != 200:
        fail(f"上传失败 {response.status_code}: {response.text[:300]}")
    job_id = response.json()["job_id"]
    data: dict = {}
    while time.perf_counter() - started < timeout:
        data = client.get(f"{base_url}/api/jobs/{job_id}").json()
        if data.get("job", {}).get("status") in ("done", "failed"):
            break
        time.sleep(0.2)
    if data.get("job", {}).get("status") != "done":
        job = data.get("job") or {}
        fail(f"任务未完成: status={job.get('status')} error={job.get('error')}")
    return time.perf_counter() - started, data


def measure_latency() -> tuple[list[float], list[float]]:
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
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
        env=os.environ.copy(),
    )
    try:
        wait_ready(base_url, process)
        with httpx.Client(timeout=180.0) as client:
            # 预热：加载 OCR / ASR 模型，不计入采样
            upload_and_wait(client, base_url, IMAGE, 120.0)
            upload_and_wait(client, base_url, VIDEO, 240.0)

            image_times = []
            for _ in range(IMAGE_RUNS):
                elapsed, _ = upload_and_wait(client, base_url, IMAGE, 120.0)
                image_times.append(elapsed)

            video_times = []
            for _ in range(VIDEO_RUNS):
                elapsed, data = upload_and_wait(client, base_url, VIDEO, 240.0)
                video_times.append(elapsed)
                if not data.get("findings"):
                    fail("15s 视频未产出任何信号，检查 mock fixture")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
    return image_times, video_times


def check_eval() -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "app.eval.run_eval"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        fail(f"评测运行失败: {(result.stdout + result.stderr)[-800:]}")
    report_path = ROOT / "eval_report.json"
    if not report_path.exists():
        fail("缺少 eval_report.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    full = report["groups"]["full"]
    if full["clean_false_positive_cases"] != 0:
        fail(
            "干净集出现 P0/P1 误报: "
            f"{full['clean_false_positive_cases']}/{full['clean_cases']} "
            f"{full.get('false_positive_cases')}"
        )
    return full


def check_demo() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "demo_check.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        fail(f"五场景回放失败: {(result.stdout + result.stderr)[-800:]}")


def readiness() -> tuple[list[str], list[str]]:
    from app.config import settings

    configured: list[str] = []
    missing: list[str] = []
    checks = {
        "Anthropic": bool(settings.anthropic_api_key),
        "Google Vision": bool(
            settings.google_vision_api_key or settings.google_application_credentials
        ),
        "AWS Rekognition": bool(
            settings.aws_access_key_id and settings.aws_secret_access_key
        ),
        "Feishu": bool(settings.feishu_app_id and settings.feishu_app_secret),
        "ACRCloud": bool(
            settings.acrcloud_host
            and settings.acrcloud_access_key
            and settings.acrcloud_access_secret
        ),
    }
    for name, ready in checks.items():
        (configured if ready else missing).append(name)
    return configured, missing


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if not IMAGE.exists() or not VIDEO.exists():
        fail("缺少验收素材，先运行 make p0 / make p1 生成")

    image_times, video_times = measure_latency()
    image_p50 = percentile(image_times, 0.5)
    image_p90 = percentile(image_times, 0.9)
    video_p50 = percentile(video_times, 0.5)
    video_p90 = percentile(video_times, 0.9)
    if image_p50 > IMAGE_P50_LIMIT:
        fail(f"图片 p50 {image_p50:.2f}s 超过 {IMAGE_P50_LIMIT}s")
    if video_p50 > VIDEO_P50_LIMIT:
        fail(f"15s 视频 p50 {video_p50:.2f}s 超过 {VIDEO_P50_LIMIT}s")

    full = check_eval()
    check_demo()
    configured, missing = readiness()
    docker_ready = shutil.which("docker") is not None

    print("[OK] Phase 8 验收通过")
    print(
        f"     图片延迟：p50 {image_p50:.2f}s / p90 {image_p90:.2f}s"
        f"（{IMAGE_RUNS} 次，目标 <{IMAGE_P50_LIMIT:.0f}s）"
    )
    print(
        f"     15s 视频：p50 {video_p50:.2f}s / p90 {video_p90:.2f}s"
        f"（{VIDEO_RUNS} 次，目标 <{VIDEO_P50_LIMIT:.0f}s）"
    )
    print(
        f"     干净集误报：{full['clean_false_positive_cases']}/{full['clean_cases']}"
        f"；植入集 P0/P1 命中 {full['truth_hit']}/{full['truth_risk']}"
    )
    print("     五场景离线回放：通过")
    print(
        "     实时凭证："
        + (f"已配置 {', '.join(configured)}" if configured else "无")
        + (f"；待配置 {', '.join(missing)}" if missing else "")
    )
    print(f"     部署验证：{'Docker 已就绪' if docker_ready else 'BLOCKED（本机无 Docker）'}")
    if missing or not docker_ready:
        print("     阻塞项需要在有凭证 / Docker 的环境重跑本脚本，不影响离线验收结果。")


if __name__ == "__main__":
    main()
