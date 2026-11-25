"""抽取层：图片路径的产物结构与落盘（OCR/pHash 用假实现，保证离线且快）。"""

import json
from pathlib import Path

from app.config import settings
from app.models import Asset
from app.pipeline import extract, ocr

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "fixtures" / "media" / "sample_image.png"


def _fake_ocr(_path, engine=None):
    return ocr.OcrResult(
        status="ok",
        engine="fake",
        lines=[
            ocr.OcrLine("SALE 50% OFF", 0.99, (0.1, 0.8, 0.5, 0.9)),
            ocr.OcrLine("LOGO", 0.9, (0.7, 0.1, 0.8, 0.2)),
        ],
        elapsed_ms=1,
    )


def test_extract_image_writes_json(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(ocr, "ocr_image", _fake_ocr)
    monkeypatch.setattr(extract.scene, "phash_of", lambda _path: "0123456789abcdef")

    asset = Asset(
        sha256="testsha",
        media_type="image",
        path=str(SAMPLE),
        width=640,
        height=360,
    )
    result = extract.extract_asset(asset)

    assert result["frames"][0]["phash"] == "0123456789abcdef"
    assert result["frames"][0]["text"].startswith("SALE")
    assert result["frames"][0]["text_area"] > 0
    assert result["ocr"]["status"] == "ok"
    assert result["asr"] == {"status": "skipped"}
    assert result["warnings"] == []

    path = extract.extraction_json_path("testsha")
    assert path.exists()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["asset"]["sha256"] == "testsha"
    assert on_disk["ocr"]["line_count"] == 2

    summary = extract.summary(on_disk)
    assert summary["frame_count"] == 1
    assert summary["ocr_status"] == "ok"
    assert summary["ocr_text_len"] > 0
    assert extract.has_extraction("testsha")


def test_extract_missing_file_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    asset = Asset(sha256="missing", media_type="image", path=str(tmp_path / "nope.png"))
    try:
        extract.extract_asset(asset)
    except FileNotFoundError:
        return
    raise AssertionError("缺失素材应抛 FileNotFoundError")
