"""授权书字段抽取单测：标签行解析、日期/布尔归一、低置信转人工。"""

from datetime import date
from pathlib import Path

import pytest
from app.ledger import extract

SAMPLE = """素材授权书（演示样本）
授权方（Licensor）：Star Music Publishing Ltd.
被授权方（Licensee）：Flyer AdCo
授权类型（Scope）：music
授权范围（Scope Items）：示例曲目《Neon Sky》、示例曲目《Sunrise》
地域范围（Territory）：美国、欧盟
媒介范围（Media）：TikTok、Meta
生效日期（Start）：2026-01-01
到期日期（End）：2027-12-31
可转授权（Sublicensable）：否
限制条款（Restrictions）：不得用于敏感品类
"""


def test_parse_fields_from_labelled_lines():
    fields = extract.parse_fields(SAMPLE)
    assert fields.licensor == "Star Music Publishing Ltd."
    assert fields.licensee == "Flyer AdCo"
    assert fields.scope_type == "music"
    assert len(fields.scope_items) == 2
    assert fields.territory == ["美国", "欧盟"]
    assert fields.media == ["TikTok", "Meta"]
    assert fields.start == date(2026, 1, 1)
    assert fields.end == date(2027, 12, 31)
    assert fields.sublicensable is False
    assert fields.restrictions == ["不得用于敏感品类"]
    assert fields.missing_fields == []
    assert fields.field_confidence["end"] == pytest.approx(0.95)


def test_parse_accepts_fullwidth_colon_and_missing_parentheses():
    text = "地域范围：东南亚\n生效日期: 2026/2/1\n到期日期: 2027年3月5日\n"
    fields = extract.parse_fields(text)
    assert fields.territory == ["东南亚"]
    assert fields.start == date(2026, 2, 1)
    assert fields.end == date(2027, 3, 5)


def test_parse_reports_missing_key_fields():
    fields = extract.parse_fields("授权方：某公司\n地域范围：美国\n")
    assert set(fields.missing_fields) == {"scope_type", "start", "end"}


def test_date_and_bool_helpers():
    assert extract.parse_date("2026年1月2日") == date(2026, 1, 2)
    assert extract.parse_date("not a date") is None
    assert extract.parse_date("2026-13-40") is None
    assert extract.parse_bool("是") is True
    assert extract.parse_bool("否") is False
    assert extract.parse_bool("也许") is None


def test_low_confidence_text_goes_to_needs_review():
    extraction = extract.LicenseExtraction(
        fields=extract.parse_fields(SAMPLE, base_confidence=extract.IMAGE_TEXT),
        text=extract.TextResult(SAMPLE, "image", extract.IMAGE_TEXT),
    )
    record = extract.build_record(extraction, source_doc="scan.png")
    assert record.status == "needs_review"


def test_build_record_marks_expired_licence():
    extraction = extract.LicenseExtraction(
        fields=extract.parse_fields(SAMPLE.replace("2027-12-31", "2024-12-31")),
        text=extract.TextResult(SAMPLE, "pdf", extract.PDF_TEXT),
    )
    record = extract.build_record(extraction, source_doc="licence.pdf")
    assert record.status == "expired"
    assert record.end == date(2024, 12, 31)


def test_pdf_text_layer_is_extracted(tmp_path: Path):
    pymupdf = pytest.importorskip("pymupdf")
    latin = (
        "Licensor: Star Music Publishing Ltd.\n"
        "Licensee: Flyer AdCo\n"
        "Scope: music\n"
        "Scope Items: Neon Sky, Sunrise\n"
        "Territory: US, EU\n"
        "Media: TikTok, Meta\n"
        "Start: 2026-01-01\n"
        "End: 2027-12-31\n"
        "Sublicensable: No\n"
        "Restrictions: no resale\n"
    )
    path = tmp_path / "licence.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    y = 80
    for line in latin.splitlines():
        page.insert_text((64, y), line, fontsize=10)
        y += 18
    doc.save(path)
    doc.close()

    text = extract.extract_text(path)
    assert text.source == "pdf"
    assert text.confidence == extract.PDF_TEXT
    extraction = extract.extract_license(path)
    assert extraction.fields.territory == ["US", "EU"]
    assert extraction.fields.missing_fields == []
