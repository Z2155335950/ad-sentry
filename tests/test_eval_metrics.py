"""评测指标：bbox IoU、时间重叠、匹配、Wilson 区间与汇总计数。"""

from app.eval import metrics
from app.eval.metrics import EvalCase


def test_bbox_iou():
    assert metrics.bbox_iou("0,0,1,1", "0,0,1,1") == 1.0
    assert metrics.bbox_iou("0,0,0.5,0.5", "0.5,0.5,1,1") == 0.0
    assert metrics.bbox_iou("0,0,2,2", "1,1,3,3") == 1 / 7
    assert metrics.bbox_iou(None, "0,0,1,1") == 0.0


def test_time_overlap():
    assert metrics.time_overlap(0.0, 10.0, 0.0, 10.0) == 1.0
    assert metrics.time_overlap(0.0, 4.0, 0.0, 10.0) == 0.4
    assert metrics.time_overlap(5.0, 6.0, 0.0, 10.0) == 0.1
    assert metrics.time_overlap(None, None, 0.0, 10.0) == 0.0


def test_matches_by_ref_bbox_time_and_category():
    finding = {
        "category": "logo",
        "level": "P1",
        "evidence": [
            {"kind": "reference", "ref_record_id": "trademark:uspto:123"},
            {"kind": "bbox", "bbox": "0.10,0.10,0.30,0.30"},
            {"kind": "timestamp", "t_start": 3.0, "t_end": 5.0},
        ],
    }
    assert metrics.matches(finding, {"category": "logo", "ref": "uspto"})
    assert metrics.matches(finding, {"category": "logo", "bbox": "0.12,0.12,0.32,0.32"})
    assert metrics.matches(finding, {"category": "logo", "t_start": 3.5, "t_end": 4.5})
    assert metrics.matches(finding, {"category": "logo"})
    assert not metrics.matches(finding, {"category": "celebrity"})
    assert not metrics.matches(finding, {"category": "logo", "ref": "acrcloud"})


def test_wilson_interval_bounds():
    low, high = metrics.wilson(5, 10)
    assert 0.0 <= low < 0.5 < high <= 1.0
    assert metrics.wilson(0, 0) == (0.0, 0.0)


def test_summarize_counts():
    clean_case = EvalCase("c1", "a.png", True, [])
    planted_case = EvalCase(
        "c2", "b.mp4", False, [{"category": "logo", "ref": "trademark", "level": "P0"}]
    )
    results = [
        {
            "case": clean_case,
            "findings": [{"category": "logo", "level": "P1", "evidence": []}],
            "latency_s": 0.2,
            "cost_usd": 0.0,
            "calls": 3,
        },
        {
            "case": planted_case,
            "findings": [
                {
                    "category": "logo",
                    "level": "P1",
                    "evidence": [{"kind": "reference", "ref_record_id": "trademark:uspto:1"}],
                }
            ],
            "latency_s": 1.0,
            "cost_usd": 0.01,
            "calls": 5,
        },
    ]
    summary = metrics.summarize(results)
    assert summary["clean_cases"] == 1
    assert summary["clean_false_positive_cases"] == 1
    assert summary["false_positive_cases"] == ["c1"]
    assert summary["truth_risk"] == 1
    assert summary["truth_hit"] == 1
    assert summary["predicted_risk"] == 2
    assert summary["predicted_hit"] == 1
    assert summary["total_calls"] == 8
    assert summary["total_cost_usd"] == 0.01
    assert summary["per_category"]["logo"]["pred"] == 2
