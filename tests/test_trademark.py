"""商标词表：归一化、词边界、模糊规则与定级（PROMPT §8.6）。"""

import pytest
from app.tools import trademark
from app.tools.trademark import Match


@pytest.fixture(scope="module")
def catalog():
    return trademark.load_catalog()


def test_normalize_fullwidth_case_and_symbols():
    assert trademark.normalize_text("ＮＩＫＥ®") == "nike"
    assert trademark.normalize_text("  Nike™  ") == "nike"
    assert trademark.normalize_text("耐克®") == "耐克"


def test_nike_exact_hit(catalog):
    matches = trademark.match_text("Big NIKE sale", catalog=catalog)
    assert any(match.brand.mark == "Nike" for match in matches)


def test_bike_does_not_match_nike(catalog):
    matches = trademark.match_text("bike-friendly city tour", catalog=catalog)
    assert all(match.brand.mark != "Nike" for match in matches)


def test_short_words_require_exact_match(catalog):
    assert not any(
        match.brand.mark == "Nike" for match in trademark.match_text("nikee", catalog=catalog)
    )


def test_long_word_allows_one_edit(catalog):
    matches = trademark.match_text("adiddas running shoes", catalog=catalog)
    fuzzy = [match for match in matches if match.brand.mark == "adidas"]
    assert fuzzy and fuzzy[0].method == "fuzzy"


def test_chinese_alias(catalog):
    matches = trademark.match_text("耐克新款跑鞋", catalog=catalog)
    assert any(match.brand.mark == "Nike" and match.method == "cjk" for match in matches)


def test_format_bbox_matches_evidence_column():
    assert trademark.format_bbox([0.1, 0.2, 0.3, 0.4]) == "0.1000,0.2000,0.3000,0.4000"
    assert trademark.format_bbox("0.1,0.2,0.3,0.4") == "0.1,0.2,0.3,0.4"
    assert trademark.format_bbox(None) is None


def _frame(text: str, bbox="0.1,0.2,0.5,0.3", timestamp=6.5):
    return {
        "index": 1,
        "timestamp": timestamp,
        "scene_index": 1,
        "ocr": [
            {"text": text, "confidence": 0.95, "bbox": [0.1, 0.2, 0.5, 0.3]},
        ],
    }


def test_signals_include_registry_bbox_and_timestamp(catalog):
    signals = trademark.trademark_signals([_frame("NIKE")], catalog=catalog)
    assert len(signals) == 1
    signal = signals[0]
    assert signal["category"] == "trademark_text"
    assert signal["level"] == "P1"
    assert "侵权" not in signal["suggestion"]
    kinds = {ev["kind"] for ev in signal["evidence"]}
    assert {"reference", "bbox", "timestamp"} <= kinds
    reference = next(ev for ev in signal["evidence"] if ev["kind"] == "reference")
    assert "6124779" in reference["ref_record_id"]
    assert reference["ref_url"].startswith("https://")


def test_signals_clean_text_emit_nothing(catalog):
    assert trademark.trademark_signals([_frame("bike-friendly tour")], catalog=catalog) == []


def test_generic_terms_are_p3(catalog):
    signals = trademark.trademark_signals([_frame("PLUS")], catalog=catalog)
    assert signals and signals[0]["level"] == "P3"


def test_asr_transcript_hit_without_bbox(catalog):
    signals = trademark.trademark_signals([], "this ad features Nike", catalog=catalog)
    assert signals and signals[0]["level"] == "P1"
    assert all(ev["kind"] != "bbox" for ev in signals[0]["evidence"])


def test_competitor_terms_disabled_by_default(catalog):
    assert not trademark.match_text("Under Armour", catalog=catalog)
    enabled = trademark.Catalog(
        brands=catalog.brands,
        generic_terms=catalog.generic_terms,
        competitors=catalog.competitors,
        competitors_enabled=True,
    )
    matches = trademark.match_text(
        "Under Armour", catalog=enabled, include_competitors=True
    )
    assert matches and matches[0].brand.tier == "competitor"


def test_brand_index_has_no_duplicate_alias_per_brand(catalog):
    entries = trademark.brand_index(catalog)
    keys = {(brand.mark, normalized) for brand, normalized, _ in entries}
    assert len(keys) == len(entries)
    assert all(isinstance(match, Match) for match in trademark.match_text("Nike", catalog=catalog))
