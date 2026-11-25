"""证据红线与定级。PROMPT.md §2/§11：P0/P1 必须至少一条 reference|clause|license 证据。"""

from __future__ import annotations

from app.models import LEVEL_ORDER, Level

ANCHOR_KINDS = frozenset({"reference", "clause", "license"})


def has_anchor_evidence(evidences: list[dict]) -> bool:
    return any(ev.get("kind") in ANCHOR_KINDS for ev in evidences)


def cap_level_without_anchor(level: str, evidences: list[dict]) -> tuple[str, bool]:
    """只有模型判断、没有参照物证据时，P0/P1 最高降为 P2。返回 (级别, 是否降级)。"""
    if Level(level) in (Level.P0, Level.P1) and not has_anchor_evidence(evidences):
        return Level.P2.value, True
    return level, False


def highest_level(levels: list[str]) -> str | None:
    valid = [level for level in levels if level in LEVEL_ORDER]
    if not valid:
        return None
    return min(valid, key=LEVEL_ORDER.__getitem__)
