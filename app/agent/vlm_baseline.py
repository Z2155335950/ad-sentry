"""纯 VLM 基线（PROMPT §10，仅评测使用）。

生产路径从不 import 这个模块。基线数据来自 `fixtures/cache/vlm_baseline.json`：
一次性记录「让多模态模型直接看图会说哪些风险」的结果，用于对比误报与幻觉。
没有真实调用时报告里必须标注为「缓存基线」。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


def claims_for(asset_sha256: str) -> list[dict]:
    path = Path(settings.fixtures_dir) / "cache" / "vlm_baseline.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("VLM 基线缓存读取失败: %s", exc)
        return []
    return list((data.get("assets") or {}).get(asset_sha256, []))


def source(path: Path | None = None) -> str:
    target = path or (Path(settings.fixtures_dir) / "cache" / "vlm_baseline.json")
    return "cache" if target.exists() else "empty"
