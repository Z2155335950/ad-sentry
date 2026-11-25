"""OCR 结果归一化：不同后端/版本的返回结构都要拍平成统一行。"""

from app.pipeline.ocr import _normalize_box, _raw_items


def _collect(raw):
    items: list = []
    _raw_items(raw, items)
    return [(text, round(score, 2)) for _, text, score in items]


def test_rapidocr_style_rows():
    box = [[10, 20], [110, 20], [110, 60], [10, 60]]
    raw = [[box, "SALE 50% OFF", 0.97], [box, "NIKE", 0.9]]
    assert _collect(raw) == [("SALE 50% OFF", 0.97), ("NIKE", 0.9)]


def test_paddleocr_2x_nested_rows():
    box = [[10, 20], [110, 20], [110, 60], [10, 60]]
    raw = [[[box, ("限时优惠", 0.93)]]]
    assert _collect(raw) == [("限时优惠", 0.93)]


def test_paddleocr_3x_dict_result():
    raw = {
        "res": {
            "rec_texts": ["NEW ARRIVAL", "立即购买"],
            "rec_scores": [0.95, 0.88],
            "rec_polys": [
                [[0, 0], [10, 0], [10, 5], [0, 5]],
                [[20, 20], [30, 20], [30, 25], [20, 25]],
            ],
        }
    }
    assert _collect(raw) == [("NEW ARRIVAL", 0.95), ("立即购买", 0.88)]


def test_normalize_box_from_polygon_and_flat():
    box = [[10, 20], [110, 20], [110, 60], [10, 60]]
    assert _normalize_box(box, 200, 100) == (0.05, 0.2, 0.55, 0.6)
    assert _normalize_box([0, 0, 20, 50], 200, 100) == (0.0, 0.0, 0.1, 0.5)
    assert _normalize_box(None, 200, 100) is None
    assert _normalize_box(box, 0, 0) is None
