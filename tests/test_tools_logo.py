"""品牌 logo：Vision 响应归一化、三路投票定级与向量排序（PROMPT §8.3）。"""

import pytest
from app.tools import logo


def test_normalize_vision_response_maps_bbox_to_unit_range():
    payload = {
        "responses": [
            {
                "logoAnnotations": [
                    {
                        "description": "Nike",
                        "score": 0.93,
                        "boundingPoly": {
                            "vertices": [
                                {"x": 440, "y": 220},
                                {"x": 560, "y": 220},
                                {"x": 560, "y": 300},
                                {"x": 440, "y": 300},
                            ]
                        },
                    }
                ]
            }
        ]
    }
    normalized = logo.normalize_vision_response(payload, 1000, 400)
    hit = normalized["logos"][0]
    assert hit["brand"] == "Nike"
    assert hit["score"] == 0.93
    assert hit["bbox"] == [0.44, 0.55, 0.56, 0.75]


def test_three_routes_with_registry_are_p0():
    hits = logo.assess_hits(
        [{"brand": "Nike", "score": 0.93, "bbox": [0.4, 0.5, 0.6, 0.7]}],
        [{"brand": "Nike", "score": 0.88}],
        [{"brand": "Nike", "bbox": [0.42, 0.58, 0.58, 0.66]}],
    )
    assert len(hits) == 1
    signal = hits[0]
    assert signal["level"] == "P0"
    assert "Vision" in json_text(signal)
    reference = next(ev for ev in signal["evidence"] if ev["kind"] == "reference")
    assert "6124779" in reference["ref_record_id"]
    assert any(ev["kind"] == "bbox" for ev in signal["evidence"])


def test_single_vision_route_is_at_most_p2():
    hits = logo.assess_hits(
        [{"brand": "Nike", "score": 0.93, "bbox": [0.4, 0.5, 0.6, 0.7]}], [], []
    )
    assert hits and hits[0]["level"] == "P2"


def test_two_routes_without_registry_are_p1():
    hits = logo.assess_hits(
        [{"brand": "adidas", "score": 0.9, "bbox": [0.1, 0.1, 0.2, 0.2]}],
        [],
        [{"brand": "adidas", "bbox": [0.1, 0.1, 0.2, 0.2]}],
    )
    assert hits and hits[0]["level"] == "P1"
    reference = next(ev for ev in hits[0]["evidence"] if ev["kind"] == "reference")
    assert "无注册信息" in reference["note"]


def test_ocr_only_is_not_a_logo_signal():
    assert logo.assess_hits([], [], [{"brand": "Nike", "bbox": [0.4, 0.5, 0.6, 0.7]}]) == []


def test_unknown_brand_is_p2_and_marked():
    hits = logo.assess_hits(
        [{"brand": "Some Brand", "score": 0.8, "bbox": [0.1, 0.1, 0.2, 0.2]}],
        [{"brand": "Some Brand", "score": 0.7}],
        [],
    )
    assert hits and hits[0]["level"] == "P2"
    assert "词表外品牌" in json_text(hits[0])


def test_signals_add_timestamp_evidence():
    hits = logo.signals(
        [{"brand": "Nike", "score": 0.93, "bbox": [0.4, 0.5, 0.6, 0.7]}],
        [{"brand": "Nike", "score": 0.88}],
        [{"brand": "Nike", "bbox": [0.42, 0.58, 0.58, 0.66]}],
        frame={"index": 1, "timestamp": 6.5, "scene_index": 1},
    )
    assert hits
    assert any(
        ev["kind"] == "timestamp" and ev["t_start"] == 6.5 for ev in hits[0]["evidence"]
    )


def test_rank_vectors_orders_by_cosine_and_filters():
    np = pytest.importorskip("numpy")
    matrix = np.array([[1.0, 0.0], [0.0, 1.0], [0.6, 0.8]], dtype="float32")
    marks = ["Nike", "adidas", "Puma"]
    ranked = logo.rank_vectors(
        np.array([0.95, 0.05], dtype="float32"),
        matrix,
        marks,
        top_k=2,
        min_score=0.5,
    )
    assert [item["brand"] for item in ranked][0] == "Nike"
    assert all(item["score"] >= 0.5 for item in ranked)
    assert len(ranked) <= 2


def json_text(payload: dict) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)
