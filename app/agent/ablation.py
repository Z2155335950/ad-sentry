"""消融开关（PROMPT §10）。

env 键：ABLATION_VERIFIER / ABLATION_PLANNER / BASELINE_VLM_ONLY。
评测脚本直接改 `settings` 上的同名字段来跑对照；生产路径只读，不写入。
"""

from __future__ import annotations

from app.config import settings


def verifier_enabled() -> bool:
    return bool(settings.ablation_verifier)


def planner_enabled() -> bool:
    return bool(settings.ablation_planner)


def vlm_baseline_requested() -> bool:
    return bool(settings.baseline_vlm_only)


def snapshot() -> dict:
    return {
        "verifier": verifier_enabled(),
        "planner": planner_enabled(),
        "baseline_vlm_only": vlm_baseline_requested(),
    }
