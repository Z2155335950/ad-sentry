"""评测指标：匹配规则、P0/P1 precision/recall、干净集误报、Wilson 区间（PROMPT §15）。

匹配：类别一致，且满足以下任一 —— ref 命中；bbox IoU ≥ 0.3；时间重叠 ≥ 50%。
truth 只给类别时，任意同类别信号即算命中（用于小样本计数，不用于精确率）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

RISK_LEVELS = frozenset({"P0", "P1"})
IOU_MIN = 0.3
TIME_OVERLAP_MIN = 0.5


def parse_bbox(value) -> tuple[float, float, float, float] | None:
    if not value:
        return None
    if isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            return tuple(float(v) for v in value)  # type: ignore[return-value]
        except (TypeError, ValueError):
            return None
    try:
        parts = [float(p) for p in str(value).split(",")]
    except ValueError:
        return None
    return tuple(parts) if len(parts) == 4 else None  # type: ignore[return-value]


def bbox_iou(a, b) -> float:
    ra, rb = parse_bbox(a), parse_bbox(b)
    if ra is None or rb is None:
        return 0.0
    ax1, ay1, ax2, ay2 = ra
    bx1, by1, bx2, by2 = rb
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def time_overlap(finding_start, finding_end, truth_start, truth_end) -> float:
    if finding_start is None or truth_start is None:
        return 0.0
    f_end = finding_end if finding_end is not None else finding_start
    t_end = truth_end if truth_end is not None else truth_start + 1.0
    latest_start = max(finding_start, truth_start)
    earliest_end = min(f_end, t_end)
    inter = max(0.0, earliest_end - latest_start)
    span = max(1e-6, t_end - truth_start)
    return min(1.0, inter / span)


def finding_refs(finding: dict) -> list[str]:
    refs = []
    for ev in finding.get("evidence") or []:
        for key in ("ref_record_id", "ref_url", "clause_id"):
            value = ev.get(key)
            if value:
                refs.append(str(value))
    return refs


def finding_bbox(finding: dict) -> str | None:
    for ev in finding.get("evidence") or []:
        if ev.get("bbox"):
            return ev["bbox"]
    return None


def finding_times(finding: dict) -> tuple[float | None, float | None]:
    start = end = None
    for ev in finding.get("evidence") or []:
        if ev.get("t_start") is not None:
            start = float(ev["t_start"])
            end = ev.get("t_end")
            end = float(end) if end is not None else None
            break
    return start, end


def matches(finding: dict, truth: dict) -> bool:
    if finding.get("category") != truth.get("category"):
        return False
    ref = truth.get("ref")
    if ref and any(ref in candidate for candidate in finding_refs(finding)):
        return True
    if truth.get("bbox") and bbox_iou(finding_bbox(finding), truth["bbox"]) >= IOU_MIN:
        return True
    if truth.get("t_start") is not None:
        start, end = finding_times(finding)
        if time_overlap(start, end, truth["t_start"], truth.get("t_end")) >= TIME_OVERLAP_MIN:
            return True
    # 没有 ref / bbox / 时间约束时，类别一致即算命中
    return not any(truth.get(key) for key in ("ref", "bbox", "t_start"))


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total <= 0:
        return (0.0, 0.0)
    phat = successes / total
    denom = 1 + z * z / total
    centre = (phat + z * z / (2 * total)) / denom
    margin = z * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


@dataclass
class EvalCase:
    case_id: str
    path: str
    clean: bool
    truth: list[dict] = field(default_factory=list)


def _p0p1(findings: list[dict]) -> list[dict]:
    return [f for f in findings if f.get("level") in RISK_LEVELS]


def summarize(results: list[dict]) -> dict:
    """results: [{case, findings, latency_s, cost_usd, calls}]"""
    truth_total = 0
    truth_hit = 0
    pred_total = 0
    pred_hit = 0
    clean_cases = 0
    clean_fp = 0
    per_category: dict[str, dict] = {}

    for result in results:
        case: EvalCase = result["case"]
        findings = result["findings"]
        risky = _p0p1(findings)

        if case.clean:
            clean_cases += 1
            if risky:
                clean_fp += 1

        truths = [t for t in case.truth if t.get("level", "P1") in RISK_LEVELS]
        truth_total += len(truths)
        used: set[int] = set()
        for truth in truths:
            stat = per_category.setdefault(truth["category"], {"truth": 0, "hit": 0, "pred": 0})
            stat["truth"] += 1
            for index, finding in enumerate(risky):
                if index in used:
                    continue
                if matches(finding, truth):
                    used.add(index)
                    truth_hit += 1
                    stat["hit"] += 1
                    break

        pred_total += len(risky)
        for index, finding in enumerate(risky):
            stat = per_category.setdefault(
                finding["category"], {"truth": 0, "hit": 0, "pred": 0}
            )
            stat["pred"] += 1
            if index in used:
                pred_hit += 1

        for truth in case.truth:
            if truth.get("level", "P1") not in RISK_LEVELS:
                per_category.setdefault(truth["category"], {"truth": 0, "hit": 0, "pred": 0})

    latencies = sorted(r["latency_s"] for r in results)
    return {
        "cases": len(results),
        "clean_cases": clean_cases,
        "clean_false_positive_cases": clean_fp,
        "truth_risk": truth_total,
        "truth_hit": truth_hit,
        "predicted_risk": pred_total,
        "predicted_hit": pred_hit,
        "recall": truth_hit / truth_total if truth_total else 0.0,
        "recall_wilson": wilson(truth_hit, truth_total),
        "precision": pred_hit / pred_total if pred_total else 0.0,
        "precision_wilson": wilson(pred_hit, pred_total),
        "total_cost_usd": round(sum(r["cost_usd"] for r in results), 6),
        "total_calls": sum(r["calls"] for r in results),
        "latency_p50_s": _percentile(latencies, 50),
        "latency_p90_s": _percentile(latencies, 90),
        "per_category": per_category,
        "false_positive_cases": [
            r["case"].case_id for r in results if r["case"].clean and _p0p1(r["findings"])
        ],
        "missed_cases": [
            r["case"].case_id
            for r in results
            if any(
                t.get("level", "P1") in RISK_LEVELS
                for t in r["case"].truth
                if not any(matches(f, t) for f in _p0p1(r["findings"]))
            )
        ],
    }


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return round(values[0], 3)
    index = (len(values) - 1) * pct / 100
    low = math.floor(index)
    high = math.ceil(index)
    if low == high:
        return round(values[low], 3)
    weight = index - low
    return round(values[low] * (1 - weight) + values[high] * weight, 3)
