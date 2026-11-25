"""Verifier：帧定位、裁剪放大、置信合并与消融开关。"""

from app.agent import verifier
from app.config import settings
from app.models import Asset
from app.tools import base
from PIL import Image


def test_frame_for_picks_nearest_timestamp():
    signal = {"evidence": [{"kind": "timestamp", "t_start": 5.0}]}
    frames = [
        {"timestamp": 0.0, "path": "a.jpg"},
        {"timestamp": 4.8, "path": "b.jpg"},
        {"timestamp": 9.0, "path": "c.jpg"},
    ]
    assert verifier.frame_for(signal, frames)["path"] == "b.jpg"
    assert verifier.frame_for({"evidence": []}, frames) is None


def test_crop_frame_expands_and_upscales(tmp_path):
    frame = tmp_path / "frame.jpg"
    Image.new("RGB", (100, 100), (10, 20, 30)).save(frame)

    out = verifier.crop_frame(frame, "0.4,0.4,0.6,0.6", tmp_path / "out")
    assert out is not None and out.exists()

    with Image.open(out) as image:
        # 外扩 20% + 1% → 0.35~0.65（30px），再 2× 放大
        assert image.size == (60, 60)


def test_crop_frame_rejects_bad_bbox(tmp_path):
    frame = tmp_path / "frame.jpg"
    Image.new("RGB", (10, 10)).save(frame)
    assert verifier.crop_frame(frame, "not-a-bbox", tmp_path / "out") is None


def _asset(frame_path) -> Asset:
    return Asset(sha256="verifier-test", media_type="image", path=str(frame_path))


def test_verify_returns_skipped_when_ablation_off(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ablation_verifier", False)
    signal = {"category": "logo", "level": "P2", "confidence": 0.5, "evidence": []}
    stats = verifier.verify(
        _asset(tmp_path / "x.jpg"), {"frames": []}, [signal], runtime=None
    )
    assert stats["skipped"] == 1
    assert stats["calls"] == 0


def test_verify_upgrades_when_second_pass_is_stronger(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ablation_verifier", True)
    frame = tmp_path / "frame.jpg"
    Image.new("RGB", (100, 100)).save(frame)

    signal = {
        "category": "logo",
        "level": "P2",
        "confidence": 0.5,
        "dedupe_key": "logo:Nike",
        "evidence": [
            {"kind": "bbox", "bbox": "0.4,0.4,0.6,0.6"},
            {"kind": "reference", "ref_record_id": "trademark:uspto:1"},
            {"kind": "timestamp", "t_start": 0.0},
        ],
    }
    asset = _asset(frame)
    extraction = {"frames": [{"path": "frame.jpg", "timestamp": 0.0, "scene_index": 0}]}

    monkeypatch.setattr(verifier, "crop_frame", lambda *a, **k: frame)
    monkeypatch.setattr(
        verifier,
        "_rerun",
        lambda *a, **k: base.ToolResult(tool="logo", status="ok", data={}, mode="mock"),
    )
    monkeypatch.setattr(verifier, "_second_confidence", lambda *a, **k: 0.9)

    stats = verifier.verify(asset, extraction, [signal], runtime=None)

    assert stats["candidates"] == 1
    assert stats["calls"] == 1
    assert stats["upgraded"] == 1
    assert signal["confidence"] == 0.9
    assert signal["level"] == "P1"
    assert any("二次检索" in (ev.get("note") or "") for ev in signal["evidence"])


def test_verify_ignores_high_confidence_signals(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ablation_verifier", True)
    signal = {"category": "logo", "level": "P0", "confidence": 0.9, "evidence": []}
    stats = verifier.verify(
        _asset(tmp_path / "x.jpg"), {"frames": []}, [signal], runtime=None
    )
    assert stats["candidates"] == 0
    assert stats["calls"] == 0
