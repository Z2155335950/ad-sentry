"""抽取层编排：素材 → 元数据 / 音轨 / 分镜 / 关键帧 / OCR / ASR / pHash。

产物写到 `DATA_DIR/extracted/<sha256>/extraction.json`（帧图片同目录），是后续工具层
（图搜、BGM、logo、商标词）的输入。抽取本身不产生 Finding：任何单步失败只降级该信号
并记入 warnings；只有视频完全抽不出帧时才抛错，让 Job 标记 failed。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path

from app.config import settings
from app.models import Asset, MediaType
from app.pipeline import asr, ffmpeg, ocr, scene

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

StageCallback = Callable[[str], None]


def extraction_dir(sha256: str) -> Path:
    return Path(settings.data_dir) / "extracted" / sha256


def extraction_json_path(sha256: str) -> Path:
    return extraction_dir(sha256) / "extraction.json"


def has_extraction(sha256: str) -> bool:
    return extraction_json_path(sha256).exists()


def load_extraction(sha256: str) -> dict | None:
    path = extraction_json_path(sha256)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("extraction.json 读取失败: %s", path)
        return None


def _notify(on_stage: StageCallback | None, stage: str) -> None:
    if on_stage is not None:
        try:
            on_stage(stage)
        except Exception:  # 进度回调失败不能影响抽取
            logger.debug("stage 回调失败: %s", stage, exc_info=True)


def _rel(path: str | Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(settings.data_dir).resolve()).as_posix()
    except ValueError:
        return Path(path).resolve().as_posix()


def _text_area(lines: list[ocr.OcrLine]) -> float:
    return sum(
        (line.bbox[2] - line.bbox[0]) * (line.bbox[3] - line.bbox[1])
        for line in lines
        if line.bbox is not None
    )


def _empty_result(asset: Asset) -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "asset": {
            "sha256": asset.sha256,
            "media_type": asset.media_type,
            "path": _rel(asset.path),
            "width": asset.width,
            "height": asset.height,
            "duration": asset.duration,
        },
        "media": {},
        "frames": [],
        "ocr": {"status": "skipped", "engine": None, "text": "", "line_count": 0},
        "asr": {"status": "skipped"},
        "warnings": [],
    }


def _extract_image(asset: Asset, result: dict, on_stage: StageCallback | None) -> None:
    src = Path(asset.path)
    _notify(on_stage, "ocr")
    ocr_result = ocr.ocr_image(src)
    frame = scene.Frame(
        index=0,
        timestamp=0.0,
        scene_index=0,
        path=_rel(src),
        sharpness=0.0,
        phash=scene.phash_of(src),
        ocr=[line.to_dict() for line in ocr_result.lines],
        text=ocr_result.text,
        text_area=round(_text_area(ocr_result.lines), 4),
    )
    result["frames"] = [frame.to_dict()]
    result["ocr"] = ocr_result.to_dict()
    if ocr_result.status != "ok":
        result["warnings"].append(f"ocr:{ocr_result.status}")


def _extract_video(
    asset: Asset,
    result: dict,
    workdir: Path,
    on_stage: StageCallback | None,
) -> None:
    src = Path(asset.path)
    _notify(on_stage, "probe")
    info = ffmpeg.probe(src)
    result["media"] = info
    duration = info.get("duration") or asset.duration or 0.0

    if info.get("has_audio") and settings.asr_enabled:
        _notify(on_stage, "asr")
        try:
            wav_path = ffmpeg.extract_audio(src, workdir / "audio.wav")
            asr_result = asr.transcribe(wav_path)
        except Exception as exc:  # 音频抽取失败只丢 ASR 信号
            logger.warning("ASR 链路失败: %s", exc)
            asr_result = asr.AsrResult(status="error", error=str(exc)[:300])
        result["asr"] = asr_result.to_dict()
        if asr_result.status != "ok":
            result["warnings"].append(f"asr:{asr_result.status}")
    elif settings.asr_enabled:
        result["asr"] = {"status": "no_audio"}
    else:
        result["asr"] = {"status": "disabled"}

    _notify(on_stage, "scene")
    scenes = scene.detect_scenes(
        src,
        duration,
        threshold=settings.scene_threshold,
        min_scene_len=settings.scene_min_len,
    )
    result["media"]["scene_count"] = len(scenes)
    result["media"]["scenes"] = [[start, end] for start, end in scenes]

    _notify(on_stage, "frames")
    frames_dir = workdir / "frames"
    frames = scene.select_keyframes(
        src,
        scenes,
        frames_dir,
        max_frames=settings.max_frames,
    )
    if not frames:
        raise RuntimeError("未能从视频抽取任何关键帧")

    _notify(on_stage, "ocr")
    texts: list[str] = []
    engines: set[str] = set()
    ocr_ok = 0
    for frame in frames:
        ocr_result = ocr.ocr_image(frames_dir / frame.path)
        frame.ocr = [line.to_dict() for line in ocr_result.lines]
        frame.text = ocr_result.text
        frame.text_area = round(_text_area(ocr_result.lines), 4)
        if ocr_result.status == "ok":
            ocr_ok += 1
            if ocr_result.engine:
                engines.add(ocr_result.engine)
        if frame.text and frame.text not in texts:
            texts.append(frame.text)

    result["frames"] = [frame.to_dict() for frame in frames]
    result["ocr"] = {
        "status": "ok" if ocr_ok else "unavailable",
        "engine": next(iter(engines), None),
        "text": "\n".join(texts),
        "line_count": sum(len(frame.ocr) for frame in frames),
        "frames_ok": ocr_ok,
        "frames_total": len(frames),
    }
    if not ocr_ok:
        result["warnings"].append("ocr:unavailable")


def extract_asset(
    asset: Asset,
    on_stage: StageCallback | None = None,
    ocr_engine=None,
) -> dict:
    started = time.perf_counter()
    src = Path(asset.path)
    if not src.exists():
        raise FileNotFoundError(f"素材文件不存在: {src}")

    workdir = extraction_dir(asset.sha256)
    workdir.mkdir(parents=True, exist_ok=True)
    result = _empty_result(asset)

    if asset.media_type == MediaType.image.value:
        _extract_image(asset, result, on_stage)
    else:
        _extract_video(asset, result, workdir, on_stage)

    result["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    path = extraction_json_path(asset.sha256)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return result


def summary(result: dict) -> dict:
    """写 Detection / API 列表用的小结，避免把完整帧数据塞进数据库。"""
    return {
        "schema": result.get("schema"),
        "duration": (result.get("media") or {}).get("duration"),
        "scene_count": (result.get("media") or {}).get("scene_count"),
        "frame_count": len(result.get("frames") or []),
        "ocr_status": (result.get("ocr") or {}).get("status"),
        "ocr_text_len": len((result.get("ocr") or {}).get("text") or ""),
        "asr_status": (result.get("asr") or {}).get("status"),
        "asr_text_len": len((result.get("asr") or {}).get("text") or ""),
        "warnings": result.get("warnings") or [],
    }
