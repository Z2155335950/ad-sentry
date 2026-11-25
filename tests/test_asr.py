"""ASR 适配器的字段收敛（用假模型，不加载 whisper）。"""

from app.config import settings
from app.pipeline import asr


class _FakeSegment:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start
        self.end = end
        self.text = text


class _FakeInfo:
    language = "en"
    language_probability = 0.91


class _FakeModel:
    def transcribe(self, _path, **_kwargs):
        segments = [_FakeSegment(0.0, 1.4, " Big sale today. "), _FakeSegment(1.4, 2.0, "")]
        return iter(segments), _FakeInfo()


def test_transcribe_normalizes_segments(monkeypatch):
    monkeypatch.setattr(settings, "asr_enabled", True)
    result = asr.transcribe("unused.wav", model=_FakeModel())
    assert result.status == "ok"
    assert result.language == "en"
    assert result.text == "Big sale today."
    assert result.segments == [{"start": 0.0, "end": 1.4, "text": "Big sale today."}]
    assert result.to_dict()["language_probability"] == 0.91


def test_transcribe_respects_disabled_switch(monkeypatch):
    monkeypatch.setattr(settings, "asr_enabled", False)
    result = asr.transcribe("unused.wav", model=_FakeModel())
    assert result.status == "disabled"
