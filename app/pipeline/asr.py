"""ASR 适配器：faster-whisper（CPU / int8），模型懒加载并缓存。

默认模型 small（PROMPT §4）；首次使用会从 HuggingFace 下载权重到 ASR_MODEL_DIR。
下载端点可配（HF_ENDPOINT），国内网络默认走 hf-mirror。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class AsrResult:
    status: str  # ok | disabled | unavailable | error
    model: str | None = None
    language: str | None = None
    language_probability: float | None = None
    text: str = ""
    segments: list[dict] = field(default_factory=list)
    elapsed_ms: int = 0
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "model": self.model,
            "language": self.language,
            "language_probability": (
                round(self.language_probability, 3)
                if isinstance(self.language_probability, (int, float))
                else None
            ),
            "text": self.text,
            "segments": self.segments,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
        }


_model = None
_model_key: tuple[str, str] | None = None
_model_lock = threading.Lock()


def _get_model(model_size: str, compute_type: str):
    global _model, _model_key
    key = (model_size, compute_type)
    with _model_lock:
        if _model is not None and _model_key == key:
            return _model
        if settings.hf_endpoint:
            os.environ.setdefault("HF_ENDPOINT", settings.hf_endpoint)
        from faster_whisper import WhisperModel

        model_dir = Path(settings.asr_model_dir)
        model_dir.mkdir(parents=True, exist_ok=True)
        logger.info("加载 whisper 模型 %s（%s），目录 %s", model_size, compute_type, model_dir)
        _model = WhisperModel(
            model_size,
            device="cpu",
            compute_type=compute_type,
            download_root=str(model_dir),
        )
        _model_key = key
        return _model


def _segment_text(segment) -> str:
    return str(getattr(segment, "text", "") or "").strip()


def transcribe(wav_path: str | Path, model=None) -> AsrResult:
    if not settings.asr_enabled:
        return AsrResult(status="disabled", error="ASR_ENABLED=false")
    started = time.perf_counter()
    try:
        whisper = model or _get_model(settings.asr_model_size, settings.asr_compute_type)
    except Exception as exc:
        logger.warning("whisper 模型不可用: %s", exc)
        return AsrResult(
            status="unavailable",
            model=settings.asr_model_size,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            error=str(exc)[:300],
        )

    try:
        segments, info = whisper.transcribe(str(wav_path), beam_size=1, vad_filter=False)
        items: list[dict] = []
        for segment in segments:
            text = _segment_text(segment)
            if not text:
                continue
            items.append(
                {
                    "start": round(float(segment.start), 2),
                    "end": round(float(segment.end), 2),
                    "text": text,
                }
            )
    except Exception as exc:
        logger.exception("ASR 失败 %s", wav_path)
        return AsrResult(
            status="error",
            model=settings.asr_model_size,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            error=str(exc)[:300],
        )

    language = getattr(info, "language", None)
    language_probability = getattr(info, "language_probability", None)
    return AsrResult(
        status="ok",
        model=settings.asr_model_size,
        language=language,
        language_probability=language_probability,
        text=" ".join(item["text"] for item in items).strip(),
        segments=items,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )
