"""商标词与文案：离线词表匹配（PROMPT §8.6）。

不做 USPTO/EUIPO 全文检索；输入是 OCR 文本与 ASR 转写，输出是命中词 + 注册信息引用。
匹配规则：
- 拉丁词：词边界精确匹配；长词（默认 ≥6）允许 1 字符差异；短词只精确匹配，
  所以「bike」不会命中「Nike」。
- 中文：别名按 n-gram 精确包含（≥2 字）。
- 通用词（PLUS/PRO/ULTRA/MAX）只给 P3；竞品词默认不启用。
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz.distance import Levenshtein

from app.config import settings

logger = logging.getLogger(__name__)

TRADEMARK_MARK = "™®"
_LATIN_WORD = re.compile(r"[a-z0-9]+")
_CJK_RUN = re.compile(r"[一-鿿]+")
_SPACES = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """全半角、大小写、标点与商标符号归一（PROMPT §8.6）。"""
    cleaned = text or ""
    # NFKC 会把 ™ 变成 "TM"，所以先去符号再归一化
    for symbol in TRADEMARK_MARK:
        cleaned = cleaned.replace(symbol, " ")
    normalized = unicodedata.normalize("NFKC", cleaned).casefold()
    for symbol in TRADEMARK_MARK:
        normalized = normalized.replace(symbol, " ")
    normalized = re.sub(r"[^\w一-鿿]+", " ", normalized, flags=re.UNICODE)
    return _SPACES.sub(" ", normalized).strip()


@dataclass
class Brand:
    mark: str
    aliases: list[str]
    owner: str | None
    jurisdictions: list[str]
    classes: list[int]
    reg_no: str | None
    reg_office: str | None
    source_url: str | None
    tier: str
    sector: str | None
    logo: str | None = None
    note: str | None = None

    @property
    def names(self) -> list[str]:
        return [self.mark, *self.aliases]

    @property
    def has_registry(self) -> bool:
        return bool(self.reg_no)

    @property
    def registry_label(self) -> str:
        if not self.reg_no:
            return "无注册信息"
        return f"{self.reg_office or '登记'} 注册号 {self.reg_no}"


@dataclass
class CompetitorTerm:
    term: str
    aliases: list[str]
    competitor_of: str | None
    sector: str | None


@dataclass
class Catalog:
    brands: list[Brand]
    generic_terms: list[Brand]
    competitors: list[CompetitorTerm] = field(default_factory=list)
    competitors_enabled: bool = False


@dataclass
class Match:
    brand: Brand
    matched_text: str
    method: str  # exact | fuzzy | cjk
    start: int
    end: int


_catalog_cache: Catalog | None = None
_catalog_mtime: float = 0.0


def _watchlists_dir() -> Path:
    return Path(settings.watchlists_dir)


def load_catalog(force: bool = False) -> Catalog:
    """读 brands.json / competitor_terms.json；文件不变时复用缓存。"""
    global _catalog_cache, _catalog_mtime
    path = _watchlists_dir() / "brands.json"
    mtime = path.stat().st_mtime if path.exists() else 0.0
    if _catalog_cache is not None and not force and mtime == _catalog_mtime:
        return _catalog_cache
    if not path.exists():
        raise FileNotFoundError(f"缺少商标词表: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    brands: list[Brand] = []
    generic: list[Brand] = []
    for item in payload.get("brands") or []:
        brand = Brand(
            mark=str(item.get("mark") or "").strip(),
            aliases=[str(alias) for alias in item.get("aliases") or [] if alias],
            owner=item.get("owner"),
            jurisdictions=list(item.get("jurisdictions") or []),
            classes=[int(value) for value in item.get("classes") or []],
            reg_no=item.get("reg_no"),
            reg_office=item.get("reg_office"),
            source_url=item.get("source_url"),
            tier=str(item.get("tier") or "standard"),
            sector=item.get("sector"),
            logo=item.get("logo"),
            note=item.get("note"),
        )
        if not brand.mark:
            continue
        (generic if brand.tier == "generic" else brands).append(brand)

    competitors: list[CompetitorTerm] = []
    enabled: list[str] = []
    competitor_path = _watchlists_dir() / "competitor_terms.json"
    if competitor_path.exists():
        competitor_payload = json.loads(competitor_path.read_text(encoding="utf-8"))
        enabled = list(competitor_payload.get("enabled_for") or [])
        competitors = [
            CompetitorTerm(
                term=str(item.get("term") or ""),
                aliases=[str(alias) for alias in item.get("aliases") or []],
                competitor_of=item.get("competitor_of"),
                sector=item.get("sector"),
            )
            for item in competitor_payload.get("terms") or []
            if item.get("term")
        ]

    _catalog_cache = Catalog(
        brands=brands,
        generic_terms=generic,
        competitors=competitors,
        competitors_enabled=bool(enabled),
    )
    _catalog_mtime = mtime
    return _catalog_cache


def brand_index(catalog: Catalog | None = None) -> list[tuple[Brand, str, str]]:
    """(brand, normalized alias, 原始别名) 列表。"""
    catalog = catalog or load_catalog()
    entries: list[tuple[Brand, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for brand in catalog.brands + catalog.generic_terms:
        for name in brand.names:
            normalized = normalize_text(name)
            if not normalized:
                continue
            key = (brand.mark, normalized)
            if key in seen:
                continue
            seen.add(key)
            entries.append((brand, normalized, name))
    return entries


def _fuzzy_ok(candidate: str, brand_norm: str) -> bool:
    if len(brand_norm) < settings.trademark_fuzzy_min_len:
        return False
    return Levenshtein.distance(candidate, brand_norm) <= 1


def match_text(
    text: str,
    *,
    catalog: Catalog | None = None,
    include_competitors: bool = False,
) -> list[Match]:
    """在文本中找品牌词。返回按出现位置排序的匹配，同一品牌只留首个。"""
    catalog = catalog or load_catalog()
    normalized = normalize_text(text)
    if not normalized:
        return []

    matches: list[Match] = []
    covered: set[str] = set()

    for brand, brand_norm, _original in brand_index(catalog):
        if brand.mark in covered:
            continue
        if not brand_norm:
            continue
        if any("一" <= ch <= "鿿" for ch in brand_norm):
            start = normalized.find(brand_norm)
            if start >= 0 and len(brand_norm) >= 2:
                matches.append(
                    Match(brand, brand_norm, "cjk", start, start + len(brand_norm))
                )
                covered.add(brand.mark)
            continue

        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(brand_norm)}(?![a-z0-9])")
        found = pattern.search(normalized)
        if found:
            matches.append(Match(brand, found.group(0), "exact", found.start(), found.end()))
            covered.add(brand.mark)
            continue

        if brand.tier == "generic":
            continue
        for token in _LATIN_WORD.finditer(normalized):
            if _fuzzy_ok(token.group(0), brand_norm):
                matches.append(
                    Match(brand, token.group(0), "fuzzy", token.start(), token.end())
                )
                covered.add(brand.mark)
                break

    if include_competitors and catalog.competitors_enabled:
        for term in catalog.competitors:
            for name in [term.term, *term.aliases]:
                name_norm = normalize_text(name)
                if not name_norm:
                    continue
                pattern = re.compile(rf"(?<![a-z0-9]){re.escape(name_norm)}(?![a-z0-9])")
                found = pattern.search(normalized)
                if found:
                    brand = Brand(
                        mark=term.term,
                        aliases=term.aliases,
                        owner=None,
                        jurisdictions=[],
                        classes=[],
                        reg_no=None,
                        reg_office=None,
                        source_url=None,
                        tier="competitor",
                        sector=term.sector,
                    )
                    matches.append(
                        Match(brand, found.group(0), "exact", found.start(), found.end())
                    )
                    break

    matches.sort(key=lambda match: match.start)
    return matches


def format_bbox(value) -> str | None:
    """Evidence.bbox 是全项目统一的逗号分隔字符串；OCR/ Vision 给的是列表。"""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        try:
            return ",".join(f"{float(part):.4f}" for part in value)
        except (TypeError, ValueError):
            return None
    return str(value)


def resolve_brand(name: str, catalog: Catalog | None = None) -> Brand | None:
    """按品牌名/别名精确解析词表条目（logo 与商标词共用）。"""
    catalog = catalog or load_catalog()
    target = normalize_text(name)
    if not target:
        return None
    for brand, normalized, _original in brand_index(catalog):
        if normalized == target:
            return brand
    return None


def level_for_brand(brand: Brand) -> str:
    """P0 需要 high-tier + 注册信息 + 同品类投放上下文；Phase 3 没有 Campaign，
    所以商标词最高 P1（PROMPT §8.6）。"""
    if brand.tier == "generic":
        return "P3"
    if brand.tier == "competitor":
        return "P2"
    if brand.has_registry and brand.tier == "high":
        return "P1"
    return "P2"


def trademark_signals(
    frames: list[dict],
    transcript: str = "",
    *,
    catalog: Catalog | None = None,
    include_competitors: bool = False,
) -> list[dict]:
    """OCR 逐行与 ASR 转写匹配；同品牌合并，保留首个定位证据。"""
    catalog = catalog or load_catalog()
    grouped: dict[str, dict] = {}

    def absorb(match: Match, *, bbox: str | None, t_start: float | None, source: str) -> None:
        entry = grouped.get(match.brand.mark)
        if entry is None:
            entry = {
                "brand": match.brand,
                "matched": match.matched_text,
                "method": match.method,
                "bbox": bbox,
                "t_start": t_start,
                "source": source,
            }
            grouped[match.brand.mark] = entry
        elif entry["bbox"] is None and bbox is not None:
            entry["bbox"] = bbox
            entry["t_start"] = t_start
            entry["source"] = source

    for frame in frames:
        for line in frame.get("ocr") or []:
            text = line.get("text") or ""
            if not text:
                continue
            for match in match_text(
                text, catalog=catalog, include_competitors=include_competitors
            ):
                absorb(
                    match,
                    bbox=line.get("bbox"),
                    t_start=frame.get("timestamp"),
                    source="ocr",
                )

    if transcript:
        for match in match_text(
            transcript, catalog=catalog, include_competitors=include_competitors
        ):
            absorb(match, bbox=None, t_start=None, source="asr")

    signals: list[dict] = []
    for entry in grouped.values():
        brand: Brand = entry["brand"]
        level = level_for_brand(brand)
        evidence: list[dict] = []
        if brand.has_registry:
            evidence.append(
                {
                    "kind": "reference",
                    "ref_record_id": f"trademark:{brand.reg_office or 'registry'}:{brand.reg_no}",
                    "ref_url": brand.source_url,
                    "note": f"{brand.mark} · {brand.registry_label}"
                    + (f" · {brand.owner}" if brand.owner else ""),
                }
            )
        elif brand.tier == "competitor":
            evidence.append(
                {
                    "kind": "metadata",
                    "note": f"竞品词命中：{brand.mark}（对照品牌 {brand.sector or '未配置'}）",
                }
            )
        else:
            evidence.append(
                {
                    "kind": "metadata",
                    "note": f"词表命中：{brand.mark}（无注册信息，最高 P2）",
                }
            )
        if entry["bbox"]:
            evidence.append(
                {
                    "kind": "bbox",
                    "bbox": format_bbox(entry["bbox"]),
                    "note": "文字区域",
                }
            )
        if entry["t_start"] is not None:
            evidence.append(
                {
                    "kind": "timestamp",
                    "t_start": entry["t_start"],
                    "note": f"关键帧命中（{'画面文字' if entry['source'] == 'ocr' else '口播'}）",
                }
            )
        method_label = {"exact": "精确", "fuzzy": "模糊", "cjk": "中文"}[entry["method"]]
        if brand.tier == "generic":
            suggestion = f"文案包含通用词「{entry['matched']}」，仅作提示，通常不构成商标风险。"
        elif brand.tier == "competitor":
            suggestion = f"文案出现竞品词「{entry['matched']}」，请确认投放策略与平台规则。"
        else:
            suggestion = (
                f"检测到疑似商标词「{entry['matched']}」（{brand.mark}，{method_label}匹配），"
                + (
                    f"参照 {brand.registry_label}，建议核实使用授权与投放品类。"
                    if brand.has_registry
                    else "词表无注册信息，建议人工核实该用词是否涉及商标权。"
                )
            )
        signals.append(
            {
                "tool": "trademark",
                "category": "trademark_text",
                "level": level,
                "confidence": 0.86 if entry["method"] == "exact" else 0.72,
                "suggestion": suggestion,
                "dedupe_key": f"trademark_text:{brand.mark}",
                "evidence": evidence,
            }
        )
    return signals
