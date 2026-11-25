"""输出层：Finding → 飞书消息卡片 JSON（card 1.0 结构，与 PROMPT.md §12 示例一致）。"""

from __future__ import annotations

from app.models import CATEGORY_LABELS, LEVEL_ORDER

LEVEL_CARD_COLORS = {"P0": "red", "P1": "orange", "P2": "yellow", "P3": "blue"}
LEVEL_LABELS = {"P0": "高风险信号", "P1": "高风险信号", "P2": "中风险信号", "P3": "提示"}
DISCLAIMER = "本结果为风险提示，不构成法律意见。"


def _ref_line(evidences: list[dict]) -> str | None:
    for ev in evidences:
        if ev.get("kind") in ("reference", "clause", "license"):
            parts = [
                p
                for p in (ev.get("ref_record_id"), ev.get("clause_id"), ev.get("ref_url"))
                if p
            ]
            if parts:
                return " · ".join(parts)
            if ev.get("note"):
                return ev["note"]
    return None


def _loc_line(evidences: list[dict]) -> str | None:
    for ev in evidences:
        if ev.get("bbox") or ev.get("t_start") is not None:
            parts = []
            if ev.get("bbox"):
                parts.append(f"bbox({ev['bbox']})")
            if ev.get("t_start") is not None:
                parts.append(f"t={ev['t_start']:.1f}s")
            if parts:
                return " ".join(parts)
    return None


def _confidence_text(confidence: float | None) -> str:
    if isinstance(confidence, (int, float)):
        return f"{confidence:.2f}"
    return "—"


def build_card(findings: list[dict], review_urls: dict[int, str]) -> dict:
    ordered = sorted(findings, key=lambda f: LEVEL_ORDER[f["level"]])
    top = ordered[0]

    elements: list[dict] = []
    for finding in ordered:
        label = CATEGORY_LABELS.get(finding["category"], finding["category"])
        lines = [
            f"**{label}** · {finding['suggestion']}"
            f"（置信 {_confidence_text(finding.get('confidence'))}）"
        ]
        ref = _ref_line(finding.get("evidence", []))
        if ref:
            lines.append(f"参照物：{ref}")
        loc = _loc_line(finding.get("evidence", []))
        if loc:
            lines.append(f"位置：{loc}")
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})

    target_url = review_urls.get(top["id"]) or next(iter(review_urls.values()), "")
    elements.append(
        {
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "type": "primary",
                    "text": {"tag": "plain_text", "content": "查看证据链"},
                    "url": target_url,
                }
            ],
        }
    )
    elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": DISCLAIMER}]})

    title = f"{top['level']} · {LEVEL_LABELS.get(top['level'], '风险信号')} | {len(ordered)} 条信号"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": LEVEL_CARD_COLORS.get(top["level"], "blue"),
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": elements,
    }
