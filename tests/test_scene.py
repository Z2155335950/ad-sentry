"""分镜与 pHash 的纯逻辑（不依赖真实视频）。"""

from pathlib import Path

from app.pipeline import scene

ROOT = Path(__file__).resolve().parents[1]


def test_uniform_scenes_covers_whole_duration():
    scenes = scene.uniform_scenes(15.0, segment=3.0)
    assert len(scenes) == 5
    assert scenes[0][0] == 0.0
    assert scenes[-1][1] == 15.0
    for previous, current in zip(scenes, scenes[1:], strict=False):
        assert previous[1] == current[0]


def test_uniform_scenes_without_duration():
    assert scene.uniform_scenes(None) == [(0.0, 0.0)]


def test_hamming_hex():
    value = "b535b54a4a4a4b7a"
    assert scene.hamming_hex(value, value) == 0
    assert scene.hamming_hex("0" * 16, "f" * 16) == 64
    assert scene.hamming_hex(value, value[:-1]) == 64


def test_detect_scenes_falls_back_when_decode_fails(tmp_path):
    missing = tmp_path / "missing.mp4"
    scenes = scene.detect_scenes(missing, duration=9.0)
    assert scenes == scene.uniform_scenes(9.0)


def test_phash_of_sample_image_is_stable():
    digest = scene.phash_of(ROOT / "fixtures" / "media" / "sample_image.png")
    assert digest is not None
    assert len(digest) == 16
    assert scene.phash_of(ROOT / "fixtures" / "media" / "sample_image.png") == digest
