"""OCR 适配器。

PROMPT 锁定 PaddleOCR；本机依赖装不上时退到 rapidocr-onnxruntime（同样是 PP-OCR
模型的 ONNX 分发），两者都不可用则返回 status=unavailable，让上层降级而不是抛断流水线。
不同版本/后端的返回结构差异都收敛在这里。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class OcrLine:
    text: str
    confidence: float
    bbox: tuple[float, float, float, float] | None = None  # 归一化 x1,y1,x2,y2

    def to_dict(self) -> dict:
        payload = {"text": self.text, "confidence": round(self.confidence, 4)}
        if self.bbox is not None:
            payload["bbox"] = [round(v, 4) for v in self.bbox]
        return payload


@dataclass
class OcrResult:
    status: str  # ok | unavailable | error
    engine: str | None
    lines: list[OcrLine] = field(default_factory=list)
    elapsed_ms: int = 0
    error: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines if line.text)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "engine": self.engine,
            "text": self.text,
            "elapsed_ms": self.elapsed_ms,
            "line_count": len(self.lines),
            "lines": [line.to_dict() for line in self.lines],
            "error": self.error,
        }


class _Engine:
    name = "base"

    def run(self, image_path: str) -> list[tuple[object, str, float]]:
        raise NotImplementedError


class PaddleEngine(_Engine):
    name = "paddleocr"

    def __init__(self, lang: str) -> None:
        from paddleocr import PaddleOCR

        last_error: Exception | None = None
        for kwargs in (
            {"use_angle_cls": True, "lang": lang, "show_log": False},
            {"use_angle_cls": True, "lang": lang},
            {"lang": lang},
        ):
            try:
                self._ocr = PaddleOCR(**kwargs)
                return
            except Exception as exc:  # 不同版本的参数差异
                last_error = exc
        raise RuntimeError(f"PaddleOCR 初始化失败: {last_error}")

    def run(self, image_path: str) -> list[tuple[object, str, float]]:
        predict = getattr(self._ocr, "predict", None)
        try:
            if callable(predict):
                raw = predict(image_path)
            else:
                raw = self._ocr.ocr(image_path, cls=True)
        except TypeError:
            raw = self._ocr.ocr(image_path)
        items: list[tuple[object, str, float]] = []
        _raw_items(raw, items)
        return items


class RapidOcrEngine(_Engine):
    name = "rapidocr"

    def __init__(self, lang: str) -> None:  # rapidocr 自带中英模型，lang 仅保留接口
        from rapidocr_onnxruntime import RapidOCR

        self._ocr = RapidOCR()

    def run(self, image_path: str) -> list[tuple[object, str, float]]:
        raw = self._ocr(image_path)
        if isinstance(raw, tuple):  # 旧版返回 (result, elapse)
            raw = raw[0]
        items: list[tuple[object, str, float]] = []
        _raw_items(raw, items)
        return items


def _safe_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _is_box(value) -> bool:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return False
    first = value[0]
    if isinstance(first, (list, tuple)) and len(first) >= 2:
        return True
    return all(isinstance(v, (int, float)) for v in value)


def _normalize_box(box, width: int, height: int):
    if box is None or width <= 0 or height <= 0:
        return None
    try:
        if hasattr(box, "tolist"):
            box = box.tolist()
        if not isinstance(box, (list, tuple)) or not box:
            return None
        if not isinstance(box[0], (list, tuple)):
            if len(box) != 4:
                return None
            x1, y1, x2, y2 = (float(v) for v in box)
        else:
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
        return (
            round(max(0.0, min(1.0, x1 / width)), 4),
            round(max(0.0, min(1.0, y1 / height)), 4),
            round(max(0.0, min(1.0, x2 / width)), 4),
            round(max(0.0, min(1.0, y2 / height)), 4),
        )
    except Exception:
        return None


def _raw_items(node, out: list[tuple[object, str, float]]) -> None:
    """把各后端的结果结构拍平成 (box, text, score)。"""
    if node is None:
        return
    if hasattr(node, "json"):  # Paddle 3.x Result
        try:
            node = node.json() if callable(node.json) else node.json
        except Exception:
            node = None
    if node is None:
        return
    if hasattr(node, "txts") and hasattr(node, "boxes"):  # RapidOCR 新版输出对象
        boxes = node.boxes if node.boxes is not None else []
        txts = node.txts if node.txts is not None else []
        scores = node.scores if node.scores is not None else []
        for index, text in enumerate(txts):
            box = boxes[index] if index < len(boxes) else None
            score = scores[index] if index < len(scores) else 0.0
            out.append((box, str(text), _safe_float(score)))
        return
    if isinstance(node, dict):
        payload = node.get("res", node)
        if isinstance(payload, dict) and "rec_texts" in payload:
            texts = payload.get("rec_texts") or []
            scores = payload.get("rec_scores") or []
            boxes = (
                payload.get("rec_polys")
                or payload.get("dt_polys")
                or payload.get("rec_boxes")
                or []
            )
            for index, text in enumerate(texts):
                box = boxes[index] if index < len(boxes) else None
                score = scores[index] if index < len(scores) else 0.0
                out.append((box, str(text), _safe_float(score)))
            return
        for value in node.values():
            _raw_items(value, out)
        return
    if isinstance(node, (list, tuple)):
        if len(node) >= 2 and _is_box(node[0]) and isinstance(node[1], str):
            score = node[2] if len(node) > 2 else 0.0
            out.append((node[0], node[1], _safe_float(score)))
            return
        if (
            len(node) >= 2
            and _is_box(node[0])
            and isinstance(node[1], (list, tuple))
            and node[1]
            and isinstance(node[1][0], str)
        ):
            score = node[1][1] if len(node[1]) > 1 else 0.0
            out.append((node[0], node[1][0], _safe_float(score)))
            return
        for value in node:
            _raw_items(value, out)


def _image_size(image_path: str) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(image_path) as image:
            return image.size
    except Exception:
        return (0, 0)


_engine: _Engine | None = None
_engine_tried = False
_engine_lock = threading.Lock()
_run_lock = threading.Lock()


def get_engine() -> _Engine | None:
    global _engine, _engine_tried
    choice = (settings.ocr_engine or "auto").lower()
    if choice == "none":
        return None
    with _engine_lock:
        if _engine is not None or _engine_tried:
            return _engine
        order = ["paddle", "rapidocr"] if choice == "auto" else [choice]
        for name in order:
            try:
                if name == "paddle":
                    _engine = PaddleEngine(settings.ocr_lang)
                elif name == "rapidocr":
                    _engine = RapidOcrEngine(settings.ocr_lang)
                else:
                    logger.warning("未知 OCR_ENGINE=%s，忽略", name)
                    continue
                logger.info("OCR 引擎已加载: %s", _engine.name)
                return _engine
            except Exception as exc:
                logger.warning("OCR 引擎 %s 不可用: %s", name, exc)
        _engine_tried = True
        return None


def ocr_image(image_path: str | Path, engine: _Engine | None = None) -> OcrResult:
    engine = engine or get_engine()
    if engine is None:
        return OcrResult(status="unavailable", engine=None, error="未找到可用的 OCR 引擎")
    started = time.perf_counter()
    try:
        with _run_lock:
            raw_items = engine.run(str(image_path))
    except Exception as exc:
        logger.exception("OCR 失败 %s", image_path)
        return OcrResult(
            status="error",
            engine=engine.name,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            error=str(exc)[:300],
        )

    width, height = _image_size(str(image_path))
    lines = [
        OcrLine(text=text.strip(), confidence=score, bbox=_normalize_box(box, width, height))
        for box, text, score in raw_items
        if (text or "").strip()
    ]
    return OcrResult(
        status="ok",
        engine=engine.name,
        lines=lines,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )
