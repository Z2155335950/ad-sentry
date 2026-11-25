"""Verifier：对低置信信号做一次二次检索（PROMPT §10）。

只在 `0.4 ≤ confidence < 0.7` 的 copyright_image / logo 信号上触发：
bbox 外扩 20% → 裁剪 → 2× 放大 → 用同一工具重搜。

置信提升才合并（并把这一步写进证据），不提升就不动信号——mock 下同一分镜返回同一份
缓存，所以纯 mock 评测里 Verifier 不会改变结果，这一点在报告里如实标注。
单素材最多 1 轮、调用数受 `VERIFIER_MAX_CALLS` 与预算约束。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from app.agent import ablation
from app.config import settings
from app.models import Asset
from app.pipeline import extract, fuse
from app.tools import base, logo, websearch

logger = logging.getLogger(__name__)

LOW_MIN = 0.4
LOW_MAX = 0.7
UPGRADE_DELTA = 0.02
CANDIDATE_CATEGORIES = {"copyright_image", "logo"}


def _parse_bbox(value) -> tuple[float, float, float, float] | None:
    if not value:
        return None
    try:
        parts = [float(part) for part in str(value).split(",")]
    except ValueError:
        return None
    return tuple(parts) if len(parts) == 4 else None  # type: ignore[return-value]


def _bbox_of(signal: dict) -> str | None:
    for ev in signal.get("evidence") or []:
        if ev.get("bbox"):
            return ev["bbox"]
    return None


def _timestamp_of(signal: dict) -> float | None:
    for ev in signal.get("evidence") or []:
        if ev.get("t_start") is not None:
            return float(ev["t_start"])
    return None


def frame_for(signal: dict, frames: list[dict]) -> dict | None:
    target = _timestamp_of(signal)
    if target is None or not frames:
        return None
    return min(frames, key=lambda frame: abs(float(frame.get("timestamp") or 0.0) - target))


def crop_frame(frame_path: Path, bbox: str, out_dir: Path) -> Path | None:
    box = _parse_bbox(bbox)
    if box is None:
        return None
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(frame_path) as image:
            width, height = image.size
            x1, y1, x2, y2 = box
            dx = (x2 - x1) * 0.2 + 0.01
            dy = (y2 - y1) * 0.2 + 0.01
            left = max(0.0, x1 - dx)
            top = max(0.0, y1 - dy)
            right = min(1.0, x2 + dx)
            bottom = min(1.0, y2 + dy)
            pixels = (
                int(left * width),
                int(top * height),
                max(int(left * width) + 1, int(right * width)),
                max(int(top * height) + 1, int(bottom * height)),
            )
            crop = image.crop(pixels)
            crop = crop.resize((max(2, crop.width * 2), max(2, crop.height * 2)))
            out_dir.mkdir(parents=True, exist_ok=True)
            target = out_dir / f"verify_{abs(hash((frame_path.name, bbox)))}.jpg"
            crop.convert("RGB").save(target, quality=90)
            return target
    except Exception as exc:  # 裁剪失败只是跳过二次检索
        logger.debug("二次检索裁剪失败: %s", exc)
        return None


def _second_confidence(category: str, result: base.ToolResult, dedupe_key: str) -> float | None:
    if not result.usable or result.status == "empty":
        return None
    if category == "copyright_image":
        return float(websearch.assess(result.data).get("confidence") or 0.0)

    brand = dedupe_key.split(":", 1)[-1].casefold()
    scores = []
    for hit in (result.data.get("logos") or []) + (result.data.get("clip") or []):
        name = str(hit.get("brand") or "").casefold()
        if name and (name == brand or brand in name):
            scores.append(float(hit.get("score") or 0.0))
    return max(scores) if scores else None


def _rerun(
    signal: dict, asset: Asset, frame: dict, runtime: base.ToolRuntime, crop: Path
) -> base.ToolResult:
    category = signal["category"]
    runner: Callable[..., base.ToolResult] = (
        websearch.search_image if category == "copyright_image" else logo.detect
    )
    return runner(
        runtime,
        crop,
        asset_sha256=asset.sha256,
        scene_index=int(frame.get("scene_index") or 0),
        timestamp=float(frame.get("timestamp") or 0.0),
    )


def verify(
    asset: Asset,
    extraction: dict,
    signals: list[dict],
    runtime: base.ToolRuntime,
) -> dict:
    stats = {"candidates": 0, "calls": 0, "upgraded": 0, "skipped": 0}
    if not ablation.verifier_enabled():
        stats["skipped"] = len(signals)
        return stats

    frames = extraction.get("frames") or []
    limit = max(0, settings.verifier_max_calls)

    for signal in signals:
        if stats["calls"] >= limit:
            break
        if signal.get("category") not in CANDIDATE_CATEGORIES:
            continue
        confidence = signal.get("confidence")
        if not isinstance(confidence, (int, float)) or not (LOW_MIN <= confidence < LOW_MAX):
            continue
        stats["candidates"] += 1

        frame = frame_for(signal, frames)
        if frame is None:
            continue
        # 图搜信号通常没有 bbox：退化为整帧 2× 放大重搜
        bbox = _bbox_of(signal) or "0,0,1,1"
        crop = crop_frame(
            extract.frame_file(asset, frame),
            bbox,
            extract.extraction_dir(asset.sha256) / "verify",
        )
        if crop is None:
            continue

        result = _rerun(signal, asset, frame, runtime, crop)
        stats["calls"] += 1
        second = _second_confidence(signal["category"], result, signal.get("dedupe_key", ""))
        if second is None or second <= confidence + UPGRADE_DELTA:
            continue

        signal["confidence"] = round(min(0.99, second), 3)
        signal.setdefault("evidence", []).append(
            {
                "kind": "metadata",
                "note": (
                    "Verifier 二次检索：bbox 外扩 20% + 2× 放大重搜，"
                    f"置信 {confidence:.2f} → {signal['confidence']:.2f}"
                ),
            }
        )
        if (
            signal["confidence"] >= 0.7
            and signal.get("level") == "P2"
            and fuse.has_anchor_evidence(signal.get("evidence", []))
        ):
            signal["level"] = "P1"
        stats["upgraded"] += 1

    return stats
