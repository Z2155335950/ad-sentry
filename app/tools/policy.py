"""平台政策 RAG：离线 YAML + rank_bm25（PROMPT §8.7）。

约束：只引用条款原文与生效日期；无达标条款不产出；输出前校验 clause_id 存在。
不做向量库，也不让 LLM 改写条款。
"""

from __future__ import annotations

import logging
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import yaml
from rank_bm25 import BM25Plus

from app.config import settings

logger = logging.getLogger(__name__)

_REQUIRED = ("id", "platform", "section", "text", "effective_date", "url")
_LATIN = re.compile(r"[a-z0-9]+")
_CJK = re.compile(r"[一-鿿]+")


def tokenize(text: str) -> list[str]:
    """拉丁词 + 中文整段与二元组；中文查询与英文条款靠 summary_zh/tags 对齐。"""
    normalized = unicodedata.normalize("NFKC", text or "").casefold()
    tokens = _LATIN.findall(normalized)
    for run in _CJK.findall(normalized):
        tokens.append(run)
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def _doc_text(clause: dict) -> str:
    parts = [
        clause.get("text") or "",
        clause.get("section") or "",
        clause.get("summary_zh") or "",
        " ".join(clause.get("tags") or []),
    ]
    return "\n".join(parts)


@lru_cache(maxsize=4)
def _load_cached(path_str: str, mtime: float) -> tuple[list[dict], dict[str, dict]]:
    path = Path(path_str)
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    clauses: list[dict] = []
    by_id: dict[str, dict] = {}
    for index, raw in enumerate(payload.get("clauses") or []):
        missing = [field for field in _REQUIRED if not raw.get(field)]
        if missing:
            logger.warning("政策条款 %s 缺少字段 %s，跳过", raw.get("id") or index, missing)
            continue
        clause = {
            "id": str(raw["id"]),
            "platform": str(raw["platform"]),
            "section": str(raw["section"]),
            "text": str(raw["text"]),
            "summary_zh": raw.get("summary_zh") or "",
            "tags": list(raw.get("tags") or []),
            "effective_date": str(raw["effective_date"]),
            "url": str(raw["url"]),
        }
        if clause["id"] in by_id:
            raise ValueError(f"政策条款 id 重复: {clause['id']}")
        clauses.append(clause)
        by_id[clause["id"]] = clause
    return clauses, by_id


def load_clauses() -> tuple[list[dict], dict[str, dict]]:
    path = Path(settings.watchlists_dir) / "policy_clauses.yaml"
    if not path.exists():
        raise FileNotFoundError(f"缺少政策条款: {path}")
    return _load_cached(str(path), path.stat().st_mtime)


def clause_count() -> int:
    return len(load_clauses()[0])


_bm25_cache: tuple[float, BM25Plus, list[dict]] | None = None


def _index() -> tuple[BM25Plus, list[dict]]:
    global _bm25_cache
    path = Path(settings.watchlists_dir) / "policy_clauses.yaml"
    mtime = path.stat().st_mtime
    if _bm25_cache is not None and _bm25_cache[0] == mtime:
        return _bm25_cache[1], _bm25_cache[2]
    clauses, _ = load_clauses()
    corpus = [tokenize(_doc_text(clause)) or ["_"] for clause in clauses]
    index = BM25Plus(corpus)
    _bm25_cache = (mtime, index, clauses)
    return index, clauses


def search(
    query: str,
    *,
    platform: str | None = None,
    top_k: int | None = None,
    min_score: float | None = None,
) -> list[dict]:
    """返回达到阈值的条款（含 score），按分数降序。无达标条款返回 []。"""
    query = (query or "").strip()
    if not query:
        return []
    index, clauses = _index()
    by_id = {clause["id"]: clause for clause in clauses}
    tokens = tokenize(query)
    if not tokens:
        return []
    scores = index.get_scores(tokens)
    top_k = settings.policy_top_k if top_k is None else top_k
    min_score = settings.policy_min_score if min_score is None else min_score
    ranked = sorted(
        (
            (float(score), clause)
            for score, clause in zip(scores, clauses, strict=True)
            if (platform is None or clause["platform"].lower() == platform.lower())
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    selected: list[dict] = []
    for score, clause in ranked:
        if score < min_score:
            break
        verified = by_id.get(clause["id"])
        if verified is None:  # 输出前校验 clause_id 存在（PROMPT §8.7）
            continue
        selected.append({**verified, "score": round(score, 3)})
        if len(selected) >= top_k:
            break
    return selected


def policy_signals(
    text: str,
    *,
    platform: str | None = None,
    limit: int = 1,
) -> list[dict]:
    """把命中的第一条条款转成 P3 提示信号；没有达标条款就不产出。"""
    clauses = search(text, platform=platform, top_k=max(1, limit))
    signals: list[dict] = []
    for clause in clauses[:limit]:
        excerpt = clause["text"]
        if len(excerpt) > 180:
            excerpt = excerpt[:180].rstrip() + "…"
        signals.append(
            {
                "tool": "policy",
                "category": "policy",
                "level": "P3",
                "confidence": None,
                "suggestion": (
                    f"画面/口播文字可能涉及{clause['platform']}政策「{clause['section']}」，"
                    f"请对照条款原文确认（生效日期 {clause['effective_date']}）。"
                ),
                "dedupe_key": f"policy:{clause['id']}",
                "evidence": [
                    {
                        "kind": "clause",
                        "clause_id": clause["id"],
                        "ref_url": clause["url"],
                        "note": (
                            f"{clause['platform']} · {clause['section']} · "
                            f"生效 {clause['effective_date']}：{excerpt}"
                        ),
                    }
                ],
            }
        )
    return signals
