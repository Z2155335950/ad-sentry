"""BGM：窗口规划、ACRCloud 响应归一化与定级（PROMPT §8.2）。"""

import pytest
from app.tools import base, music
from app.tools.signals import music_signals


def test_window_plan_short_and_long():
    assert music.window_plan(0) == []
    assert [w["index"] for w in music.window_plan(8)] == [0]
    assert len(music.window_plan(30)) == 3
    starts = [w["start"] for w in music.window_plan(30)]
    assert starts == [0.0, 10.0, 20.0]


def test_window_plan_tail_window_covers_end():
    windows = music.window_plan(15)
    assert windows[-1]["end"] == 15.0
    assert windows[-1]["start"] == 5.0


def test_normalize_identified():
    payload = {
        "status": {"code": 0, "msg": "Success"},
        "metadata": {
            "music": [
                {
                    "title": "Track",
                    "artists": [{"name": "Artist"}],
                    "album": {"name": "Album"},
                    "label": "Label",
                    "score": 92,
                    "play_offset_ms": 1000,
                    "duration_ms": 200000,
                    "external_ids": {"isrc": "X"},
                }
            ]
        },
    }
    normalized = music.normalize_response(payload)
    assert normalized["status"] == "identified"
    assert normalized["artists"] == ["Artist"]
    assert normalized["score"] == pytest.approx(0.92)


def test_normalize_no_match_and_error():
    assert music.normalize_response({"status": {"code": 1001}})["status"] == "no_match"
    with pytest.raises(base.ToolError):
        music.normalize_response({"status": {"code": 3001, "msg": "bad"}})


def _result(data: dict) -> base.ToolResult:
    return base.ToolResult(tool="music", status="ok", data=data, mode="mock")


def test_identified_track_is_p2_with_reference_and_timestamp():
    windows = [
        {"index": 0, "start": 0.0, "end": 10.0},
        {"index": 1, "start": 10.0, "end": 20.0},
    ]
    results = [
        _result(
            {
                "status": "identified",
                "title": "Demo Track",
                "artists": ["Demo Artist"],
                "label": "Demo Label",
                "score": 0.9,
            }
        ),
        _result({"status": "no_match"}),
    ]
    signals = music_signals(windows, results)
    assert len(signals) == 1
    signal = signals[0]
    assert signal["level"] == "P2"
    assert signal["dedupe_key"] == "bgm:Demo Track"
    kinds = {ev["kind"] for ev in signal["evidence"]}
    assert {"reference", "timestamp"} <= kinds


def test_all_no_match_emits_unknown_music_not_empty():
    windows = [{"index": 0, "start": 0.0, "end": 10.0}]
    signals = music_signals(windows, [_result({"status": "no_match"})])
    assert len(signals) == 1
    assert signals[0]["dedupe_key"] == "bgm:unknown"
    assert "未识别" in signals[0]["suggestion"]
    assert all(ev["kind"] != "reference" for ev in signals[0]["evidence"])


def test_unavailable_tool_emits_nothing():
    signals = music_signals(
        [{"index": 0, "start": 0.0, "end": 10.0}],
        [base.ToolResult(tool="music", status="unavailable")],
    )
    assert signals == []


def test_live_request_sends_sample_bytes(monkeypatch, tmp_path):
    """ACRCloud 缺 sample_bytes 会返回 3006 invalid arguments（2026-11-25 实测）。"""
    from types import SimpleNamespace

    sample = b"RIFFxxxxWAVEfmt "
    wav = tmp_path / "window.wav"
    wav.write_bytes(sample)
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": {"code": 1001}}

    def fake_post(url, data=None, files=None, timeout=None):
        captured.update(url=url, data=data, files=files, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr(music.httpx, "post", fake_post)
    monkeypatch.setattr(
        music,
        "settings",
        SimpleNamespace(
            acrcloud_host="identify-cn-north-1.acrcloud.cn",
            acrcloud_access_key="key",
            acrcloud_access_secret="secret",
            timeout_music=10.0,
        ),
    )

    result = music._live_identify(wav)

    assert result["status"] == "no_match"
    assert captured["url"] == "https://identify-cn-north-1.acrcloud.cn/v1/identify"
    assert captured["data"]["sample_bytes"] == str(len(sample))
    assert captured["data"]["data_type"] == "audio"
    assert captured["data"]["signature_version"] == "1"
    assert captured["data"]["access_key"] == "key"
    assert captured["files"]["sample"][1] == sample
