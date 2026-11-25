"""分镜、关键帧选择与 pHash 去重。

帧预算（PROMPT §8.1）：每镜最多 3 帧、全片 ≤12 帧；pHash 汉明距离 ≤6 视为重复；
场景内按清晰度（Laplacian 方差）优先。工具层要 OCR 文字面积时在选定帧上再算。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

from app.pipeline import ffmpeg

logger = logging.getLogger(__name__)

PHASH_MAX_DISTANCE = 6


@dataclass
class Frame:
    index: int
    timestamp: float
    scene_index: int
    path: str  # 相对 extraction 目录
    sharpness: float
    phash: str | None
    ocr: list[dict] = field(default_factory=list)
    text: str = ""
    text_area: float = 0.0

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "timestamp": round(self.timestamp, 3),
            "scene_index": self.scene_index,
            "path": self.path,
            "sharpness": self.sharpness,
            "phash": self.phash,
            "ocr": self.ocr,
            "text": self.text,
            "text_area": round(self.text_area, 4),
        }


def uniform_scenes(
    duration: float | None, segment: float = 3.0, max_scenes: int = 8
) -> list[tuple[float, float]]:
    """PySceneDetect 不可用时的降级：按时长均匀切段。"""
    if not duration or duration <= 0:
        return [(0.0, 0.0)]
    count = min(max_scenes, max(1, math.ceil(duration / segment)))
    step = duration / count
    return [(round(i * step, 3), round((i + 1) * step, 3)) for i in range(count)]


def detect_scenes(
    video_path: str | Path,
    duration: float | None,
    threshold: float = 27.0,
    min_scene_len: int = 15,
) -> list[tuple[float, float]]:
    try:
        from scenedetect import ContentDetector, SceneManager, open_video

        video = open_video(str(video_path))
        manager = SceneManager()
        manager.add_detector(
            ContentDetector(threshold=threshold, min_scene_len=min_scene_len)
        )
        manager.detect_scenes(video)
        scenes = [
            (round(start.get_seconds(), 3), round(end.get_seconds(), 3))
            for start, end in manager.get_scene_list()
        ]
        if scenes:
            return scenes
        logger.info("PySceneDetect 未检出场景，退化为均匀分段")
    except Exception as exc:  # 解码失败或依赖缺失都走降级
        logger.warning("PySceneDetect 不可用（%s），退化为均匀分段", exc)
    return uniform_scenes(duration)


def sharpness(image_path: str | Path) -> float:
    try:
        import cv2

        image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            return 0.0
        return float(cv2.Laplacian(image, cv2.CV_64F).var())
    except Exception as exc:
        logger.debug("清晰度计算失败 %s: %s", image_path, exc)
        return 0.0


def phash_of(image_path: str | Path) -> str | None:
    try:
        import imagehash
        from PIL import Image

        with Image.open(image_path) as image:
            return str(imagehash.phash(image))
    except Exception as exc:
        logger.debug("pHash 计算失败 %s: %s", image_path, exc)
        return None


def hamming_hex(left: str, right: str) -> int:
    if len(left) != len(right):
        return 64
    return sum(bin(int(a, 16) ^ int(b, 16)).count("1") for a, b in zip(left, right, strict=True))


def _candidate_timestamps(start: float, end: float, per_scene: int) -> list[float]:
    if end <= start:
        return [start]
    # 中点优先；再补前后 1/4 点。留 0.05s 余量避免落在下一镜的黑场。
    fractions = (0.5, 0.25, 0.75)[: max(1, per_scene)]
    upper = end - 0.05
    return [round(min(upper, start + (end - start) * fraction), 3) for fraction in fractions]


@dataclass
class _Candidate:
    timestamp: float
    path: Path
    sharpness: float
    phash: str | None


def select_keyframes(
    video_path: str | Path,
    scenes: list[tuple[float, float]],
    frames_dir: str | Path,
    max_frames: int = 12,
    per_scene: int = 3,
) -> list[Frame]:
    """抽取并选出代表性帧。返回按时间排序的 Frame（path 为文件名）。"""
    frames_dir = Path(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)

    per_scene_candidates: list[list[_Candidate]] = []
    for scene_index, (start, end) in enumerate(scenes):
        candidates: list[_Candidate] = []
        for ts in _candidate_timestamps(start, end, per_scene):
            tmp = frames_dir / f".cand_{scene_index}_{ts:.2f}.jpg"
            try:
                ffmpeg.extract_frame(video_path, ts, tmp)
            except Exception as exc:
                logger.warning("场景 %s 在 %.2fs 抽帧失败: %s", scene_index, ts, exc)
                continue
            candidates.append(
                _Candidate(ts, tmp, sharpness(tmp), phash_of(tmp))
            )
        candidates.sort(key=lambda c: c.sharpness, reverse=True)
        # 场景内先去重，再保留候选池；跨场景去重在最终挑选时做
        unique: list[_Candidate] = []
        for candidate in candidates:
            if any(
                candidate.phash
                and kept.phash
                and hamming_hex(candidate.phash, kept.phash) <= PHASH_MAX_DISTANCE
                for kept in unique
            ):
                candidate.path.unlink(missing_ok=True)
                continue
            unique.append(candidate)
        per_scene_candidates.append(unique[:per_scene])

    # 轮流从各场景取帧，保证长视频后面的场景也有机会进证据；全局 pHash 去重。
    selected: list[tuple[_Candidate, int]] = []
    progressed = True
    while progressed and len(selected) < max_frames:
        progressed = False
        for scene_index, candidates in enumerate(per_scene_candidates):
            if not candidates or len(selected) >= max_frames:
                continue
            candidate = candidates.pop(0)
            if any(
                candidate.phash
                and kept.phash
                and hamming_hex(candidate.phash, kept.phash) <= PHASH_MAX_DISTANCE
                for kept, _ in selected
            ):
                candidate.path.unlink(missing_ok=True)
                progressed = True
                continue
            selected.append((candidate, scene_index))
            progressed = True

    # 清掉未入选的候选文件
    for candidates in per_scene_candidates:
        for leftover in candidates:
            leftover.path.unlink(missing_ok=True)

    selected.sort(key=lambda item: item[0].timestamp)
    frames: list[Frame] = []
    for index, (candidate, scene_index) in enumerate(selected):
        final_name = f"frame_{index:02d}_{candidate.timestamp:.2f}.jpg"
        final_path = frames_dir / final_name
        candidate.path.replace(final_path)
        frames.append(
            Frame(
                index=index,
                timestamp=candidate.timestamp,
                scene_index=scene_index,
                path=final_name,
                sharpness=round(candidate.sharpness, 1),
                phash=candidate.phash,
            )
        )
    return frames
