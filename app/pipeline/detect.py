"""工具层编排：抽取产物 → 图搜 / BGM / 名人 → 风险信号。

主干是确定性 DAG（PROMPT §10）：按媒体类型选择工具，不用 LLM 决定跳过哪些。
每个工具独立降级，单工具失败只丢该信号。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from app.config import settings
from app.models import Asset, MediaType
from app.pipeline import extract, ffmpeg, fuse
from app.tools import base, celebrity, logo, music, policy, trademark, websearch
from app.tools import signals as signal_builder

logger = logging.getLogger(__name__)

StageCallback = Callable[[str], None]


def select_frames(frames: list[dict], limit: int) -> list[dict]:
    """按分镜轮流取清晰度最高的帧，保证长视频后段也有机会进证据。"""
    if not frames or limit <= 0:
        return []
    by_scene: dict[int, list[dict]] = defaultdict(list)
    for frame in frames:
        by_scene[int(frame.get("scene_index") or 0)].append(frame)
    for candidates in by_scene.values():
        candidates.sort(key=lambda item: item.get("sharpness") or 0.0, reverse=True)

    selected: list[dict] = []
    while len(selected) < limit and any(by_scene.values()):
        for scene_index in sorted(by_scene):
            if by_scene[scene_index] and len(selected) < limit:
                selected.append(by_scene[scene_index].pop(0))
    selected.sort(key=lambda item: item.get("timestamp") or 0.0)
    return selected


def _notify(on_stage: StageCallback | None, stage: str) -> None:
    if on_stage is not None:
        try:
            on_stage(stage)
        except Exception:
            logger.debug("stage 回调失败: %s", stage, exc_info=True)


def _run_image_search(
    asset: Asset, frames: list[dict], runtime: base.ToolRuntime
) -> list[dict]:
    signals: list[dict] = []
    for frame in select_frames(frames, settings.tool_image_frames):
        result = websearch.search_image(
            runtime,
            extract.frame_file(asset, frame),
            asset_sha256=asset.sha256,
            scene_index=int(frame.get("scene_index") or 0),
            timestamp=float(frame.get("timestamp") or 0.0),
        )
        signals.extend(signal_builder.websearch_signals(result, frame=frame))
    return signals


def _run_celebrity(
    asset: Asset, frames: list[dict], runtime: base.ToolRuntime
) -> list[dict]:
    signals: list[dict] = []
    for frame in select_frames(frames, settings.tool_celebrity_frames):
        result = celebrity.recognize(
            runtime,
            extract.frame_file(asset, frame),
            asset_sha256=asset.sha256,
            scene_index=int(frame.get("scene_index") or 0),
            timestamp=float(frame.get("timestamp") or 0.0),
        )
        signals.extend(signal_builder.celebrity_signals(result, frame=frame))
    return signals


def _ocr_brand_hits(frame: dict, catalog: trademark.Catalog) -> list[dict]:
    """OCR 行 → 词表品牌命中（给 logo 投票当佐证）；通用词与竞品词不算 logo。"""
    hits: list[dict] = []
    for line in frame.get("ocr") or []:
        text = line.get("text") or ""
        if not text:
            continue
        for match in trademark.match_text(text, catalog=catalog):
            if match.brand.tier in ("generic", "competitor"):
                continue
            hits.append(
                {
                    "brand": match.brand.mark,
                    "bbox": line.get("bbox"),
                    "score": line.get("confidence"),
                }
            )
    return hits


def _run_logo(
    asset: Asset,
    frames: list[dict],
    runtime: base.ToolRuntime,
    catalog: trademark.Catalog,
) -> list[dict]:
    signals: list[dict] = []
    for frame in select_frames(frames, settings.tool_logo_frames):
        result = logo.detect(
            runtime,
            extract.frame_file(asset, frame),
            asset_sha256=asset.sha256,
            scene_index=int(frame.get("scene_index") or 0),
            timestamp=float(frame.get("timestamp") or 0.0),
        )
        data = result.data if result.usable else {}
        signals.extend(
            logo.signals(
                data.get("logos") or [],
                data.get("clip") or [],
                _ocr_brand_hits(frame, catalog),
                frame=frame,
                catalog=catalog,
            )
        )
    return signals


def _run_trademark(
    extraction: dict, catalog: trademark.Catalog
) -> list[dict]:
    transcript = (extraction.get("asr") or {}).get("text") or ""
    return trademark.trademark_signals(
        extraction.get("frames") or [], transcript, catalog=catalog
    )


def _run_policy(extraction: dict) -> list[dict]:
    ocr_text = (extraction.get("ocr") or {}).get("text") or ""
    asr_text = (extraction.get("asr") or {}).get("text") or ""
    query = " ".join(part for part in (ocr_text, asr_text) if part).strip()
    if not query:
        return []
    return policy.policy_signals(query)


def _ensure_audio(asset: Asset, extraction: dict) -> Path | None:
    media = extraction.get("media") or {}
    if asset.media_type != MediaType.video.value or not media.get("has_audio"):
        return None
    audio = extract.extraction_dir(asset.sha256) / "audio.wav"
    if audio.exists() and audio.stat().st_size > 0:
        return audio
    try:
        return ffmpeg.extract_audio(asset.path, audio)
    except Exception as exc:
        logger.warning("BGM 抽音频失败: %s", exc)
        return None


def _run_bgm(asset: Asset, extraction: dict, runtime: base.ToolRuntime) -> list[dict]:
    audio = _ensure_audio(asset, extraction)
    if audio is None:
        return []
    media = extraction.get("media") or {}
    duration = media.get("duration") or asset.duration or 0.0
    windows, results = music.identify(
        runtime, audio, duration, asset_sha256=asset.sha256
    )
    return signal_builder.music_signals(windows, results)


def run(
    asset: Asset,
    extraction: dict,
    runtime: base.ToolRuntime,
    on_stage: StageCallback | None = None,
) -> list[dict]:
    if not settings.tools_enabled:
        return []
    frames = extraction.get("frames") or []
    catalog = trademark.load_catalog()
    signals: list[dict] = []

    _notify(on_stage, "image_search")
    signals.extend(_run_image_search(asset, frames, runtime))

    _notify(on_stage, "celebrity")
    signals.extend(_run_celebrity(asset, frames, runtime))

    _notify(on_stage, "logo")
    signals.extend(_run_logo(asset, frames, runtime, catalog))

    _notify(on_stage, "trademark")
    signals.extend(_run_trademark(extraction, catalog))

    _notify(on_stage, "bgm")
    signals.extend(_run_bgm(asset, extraction, runtime))

    _notify(on_stage, "policy")
    signals.extend(_run_policy(extraction))

    return fuse.dedupe_signals(signals)
