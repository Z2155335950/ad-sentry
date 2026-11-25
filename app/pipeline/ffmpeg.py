"""ffmpeg / ffprobe 定位与调用。

本机通常没有系统 ffmpeg；imageio-ffmpeg 的 wheel 自带二进制作为兜底，
这样媒体层不依赖系统安装。三者都找不到时抛 MediaToolUnavailable，
由上层决定降级方式，不在导入期崩溃。
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class MediaToolUnavailable(RuntimeError):
    """ffmpeg/ffprobe 均不可用。"""


@lru_cache
def ffmpeg_path() -> str | None:
    candidates = [settings.ffmpeg_bin, shutil.which("ffmpeg")]
    if not any(candidates):
        try:
            import imageio_ffmpeg

            candidates.append(imageio_ffmpeg.get_ffmpeg_exe())
        except Exception as exc:  # 依赖未安装或平台无 wheel
            logger.warning("imageio-ffmpeg 不可用: %s", exc)
    return next((c for c in candidates if c), None)


@lru_cache
def ffprobe_path() -> str | None:
    candidates = [settings.ffprobe_bin, shutil.which("ffprobe")]
    return next((c for c in candidates if c), None)


def available() -> bool:
    return ffmpeg_path() is not None


def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_NO_WINDOW,
    )


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fps(raw: str | None) -> float | None:
    if not raw:
        return None
    if "/" in raw:
        num, den = raw.split("/", 1)
        n, d = _to_float(num), _to_float(den)
        if not n or not d:
            return None
        return round(n / d, 3)
    return _to_float(raw)


def _parse_ffprobe(data: dict) -> dict:
    info: dict = {
        "duration": None,
        "width": None,
        "height": None,
        "fps": None,
        "has_audio": False,
        "video_codec": None,
        "audio_codec": None,
        "probe_backend": "ffprobe",
    }
    info["duration"] = _to_float((data.get("format") or {}).get("duration"))
    for stream in data.get("streams") or []:
        kind = stream.get("codec_type")
        if kind == "video" and info["width"] is None:
            info["width"] = stream.get("width")
            info["height"] = stream.get("height")
            info["video_codec"] = stream.get("codec_name")
            info["fps"] = _fps(stream.get("avg_frame_rate"))
            if info["duration"] is None:
                info["duration"] = _to_float(stream.get("duration"))
        elif kind == "audio":
            info["has_audio"] = True
            info["audio_codec"] = stream.get("codec_name")
    return info


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_SIZE_RE = re.compile(r"Video:.*?\b(\d{2,5})x(\d{2,5})\b")
_VIDEO_CODEC_RE = re.compile(r"Video:\s*([A-Za-z0-9_]+)")
_AUDIO_CODEC_RE = re.compile(r"Audio:\s*([A-Za-z0-9_]+)")
_FPS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*fps")


def _parse_ffmpeg_stderr(stderr: str) -> dict:
    info: dict = {
        "duration": None,
        "width": None,
        "height": None,
        "fps": None,
        "has_audio": "Audio:" in stderr,
        "video_codec": None,
        "audio_codec": None,
        "probe_backend": "ffmpeg",
    }
    match = _DURATION_RE.search(stderr)
    if match:
        hours, minutes, seconds = match.groups()
        info["duration"] = round(
            int(hours) * 3600 + int(minutes) * 60 + float(seconds), 3
        )
    match = _SIZE_RE.search(stderr)
    if match:
        info["width"], info["height"] = int(match.group(1)), int(match.group(2))
    match = _VIDEO_CODEC_RE.search(stderr)
    if match:
        info["video_codec"] = match.group(1)
    match = _AUDIO_CODEC_RE.search(stderr)
    if match:
        info["audio_codec"] = match.group(1)
    match = _FPS_RE.search(stderr)
    if match:
        info["fps"] = _to_float(match.group(1))
    return info


def probe(path: str | Path) -> dict:
    """读取时长、分辨率、帧率、是否有音轨。"""
    src = str(path)
    if ffprobe_path():
        result = _run(
            [
                str(ffprobe_path()),
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                src,
            ],
            timeout=20,
        )
        if result.returncode == 0:
            return _parse_ffprobe(json.loads(result.stdout or "{}"))
        logger.warning("ffprobe 失败，退化为解析 ffmpeg 输出: %s", result.stderr[:200])

    ffmpeg = ffmpeg_path()
    if ffmpeg is None:
        raise MediaToolUnavailable("未找到 ffmpeg/ffprobe，无法读取媒体信息")
    result = _run([ffmpeg, "-hide_banner", "-i", src], timeout=20)
    return _parse_ffmpeg_stderr(result.stderr or "")


def extract_audio(
    src: str | Path,
    dst: str | Path,
    sample_rate: int = 16000,
    timeout: float = 60,
) -> Path:
    """抽 16k 单声道 PCM，ASR 与后续 ACRCloud 共用。"""
    ffmpeg = ffmpeg_path()
    if ffmpeg is None:
        raise MediaToolUnavailable("未找到 ffmpeg，无法抽取音频")
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    result = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(src),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            str(dst),
        ],
        timeout=timeout,
    )
    if result.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        raise RuntimeError(f"抽音频失败: {(result.stderr or '').strip()[:300]}")
    return dst


def extract_frame(
    src: str | Path,
    timestamp: float,
    dst: str | Path,
    timeout: float = 20,
) -> Path:
    ffmpeg = ffmpeg_path()
    if ffmpeg is None:
        raise MediaToolUnavailable("未找到 ffmpeg，无法抽帧")
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    result = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(src),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(dst),
        ],
        timeout=timeout,
    )
    if result.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        raise RuntimeError(f"抽帧失败 t={timestamp}: {(result.stderr or '').strip()[:300]}")
    return dst


def run_raw(args: list[str], timeout: float = 120) -> subprocess.CompletedProcess:
    """给 fixture 生成脚本用的逃生口；业务代码不要直接用。"""
    ffmpeg = ffmpeg_path()
    if ffmpeg is None:
        raise MediaToolUnavailable("未找到 ffmpeg")
    return _run([ffmpeg, *args], timeout=timeout)
