"""BGM：ACRCloud 音频指纹（PROMPT §8.2）。

窗口策略：头部 / 中部 / 尾部各 10 秒；先算窗口哈希，命中 ToolCache 不再计费。
mock 读 `fixtures/cache/acrcloud.json`（按素材 sha256 + 窗口序号）；
live 未配置 ACRCloud 时返回 unavailable，不阻塞主流程。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
import wave
from pathlib import Path

import httpx

from app.config import settings
from app.tools import base

logger = logging.getLogger(__name__)

COST_PER_IDENTIFY_USD = 0.008


def window_plan(
    duration: float | None,
    count: int | None = None,
    seconds: float | None = None,
) -> list[dict]:
    count = settings.tool_music_windows if count is None else count
    seconds = settings.music_window_seconds if seconds is None else seconds
    total = float(duration or 0.0)
    if total <= 0:
        return []
    count = max(1, count)
    if total <= seconds:
        return [{"index": 0, "start": 0.0, "end": round(total, 3)}]
    if count == 1:
        return [{"index": 0, "start": 0.0, "end": round(seconds, 3)}]
    middle = max(0.0, (total - seconds) / 2)
    candidates = [0.0, middle, max(0.0, total - seconds)]
    starts = sorted({round(start, 3) for start in candidates})[:count]
    return [
        {"index": i, "start": start, "end": round(min(total, start + seconds), 3)}
        for i, start in enumerate(starts)
    ]


def _slice_wav(src: Path, dst: Path, start: float, seconds: float) -> Path:
    with wave.open(str(src), "rb") as reader:
        rate = reader.getframerate()
        reader.setpos(min(int(start * rate), reader.getnframes()))
        frames = reader.readframes(int(seconds * rate))
        params = reader.getparams()
    dst.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dst), "wb") as writer:
        writer.setnchannels(params.nchannels)
        writer.setsampwidth(params.sampwidth)
        writer.setframerate(params.framerate)
        writer.writeframes(frames)
    return dst


def _signature(access_key: str, access_secret: str, timestamp: str) -> str:
    message = f"POST\n/v1/identify\n{access_key}\naudio\n1\n{timestamp}"
    digest = hmac.new(
        access_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha1
    ).digest()
    return base64.b64encode(digest).decode()


def normalize_response(payload: dict) -> dict:
    status = payload.get("status") or {}
    code = int(status.get("code", -1))
    if code == 1001:
        return {"provider": "acrcloud", "status": "no_match", "code": code}
    if code != 0:
        message = status.get("msg") or "unknown"
        raise base.ToolError(f"ACRCloud code={code} {message}")
    music = (payload.get("metadata") or {}).get("music") or []
    if not music:
        return {"provider": "acrcloud", "status": "no_match", "code": code}
    track = music[0]
    score = track.get("score")
    return {
        "provider": "acrcloud",
        "status": "identified",
        "title": track.get("title"),
        "artists": [a.get("name") for a in track.get("artists") or [] if a.get("name")],
        "album": (track.get("album") or {}).get("name"),
        "label": track.get("label"),
        "score": round(float(score) / 100, 3) if isinstance(score, (int, float)) else None,
        "play_offset_ms": track.get("play_offset_ms"),
        "duration_ms": track.get("duration_ms"),
        "external_ids": track.get("external_ids") or {},
    }


def _live_identify(window_path: Path) -> dict:
    if not (
        settings.acrcloud_host
        and settings.acrcloud_access_key
        and settings.acrcloud_access_secret
    ):
        raise base.ToolUnavailable("未配置 ACRCloud 凭证")
    timestamp = str(int(time.time()))
    data = {
        "access_key": settings.acrcloud_access_key,
        "data_type": "audio",
        "signature_version": "1",
        "timestamp": timestamp,
        "signature": _signature(
            settings.acrcloud_access_key, settings.acrcloud_access_secret, timestamp
        ),
    }
    with open(window_path, "rb") as handle:
        files = {"sample": (window_path.name, handle, "audio/wav")}
        response = httpx.post(
            f"https://{settings.acrcloud_host}/v1/identify",
            data=data,
            files=files,
            timeout=settings.timeout_music,
        )
    response.raise_for_status()
    return normalize_response(response.json())


def identify(
    runtime: base.ToolRuntime,
    audio_path: str | Path,
    duration: float | None,
    *,
    asset_sha256: str,
) -> tuple[list[dict], list[base.ToolResult]]:
    """返回 (窗口元数据, 每个窗口的工具结果)。"""
    windows = window_plan(duration)
    results: list[base.ToolResult] = []
    mode = base.resolve_mode("music")
    source = Path(audio_path)

    for window in windows:
        index = window["index"]

        def run(window=window, index=index) -> dict | None:
            if mode == "mock":
                entry = base.mock_entry("music", asset_sha256)
                if not entry:
                    return None
                return (entry.get("windows") or {}).get(str(index))
            sliced = _slice_wav(
                source,
                source.parent / f"music_window_{index}.wav",
                window["start"],
                window["end"] - window["start"],
            )
            return _live_identify(sliced)

        results.append(
            runtime.call(
                tool="music",
                input_hash=f"{asset_sha256}:w{index}",
                fn=run,
                cost_usd=COST_PER_IDENTIFY_USD,
                mode=mode,
                timeout=settings.timeout_music + 2,
            )
        )
    return windows, results
