"""授权文件 → 文本 → 结构化字段（PROMPT §9）。

三条文本路径：PDF 文本层（PyMuPDF）、DOCX（解包 word/document.xml）、图片 OCR
（复用抽取层引擎）。扫描版 PDF 文本层为空时渲染成图片再走 OCR。

字段解析默认走「带标签行的确定性规则」：授权书模板稳定，规则可解释、可离线复现。
配置了 `LEDGER_LLM_ENABLED` 时再用 LLM 结构化抽取补空字段；LLM 失败不影响主路径。
"""

from __future__ import annotations

import logging
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from app.config import settings
from app.ledger import status as license_status
from app.models import LicenseRecord

logger = logging.getLogger(__name__)

PDF_TEXT = 0.95
DOCX_TEXT = 0.95
IMAGE_TEXT = 0.6  # OCR 结果不足以支撑状态判定，关键字段会落进 needs_review

LIST_SPLIT_RE = re.compile(r"[、,，;；\n]")
DATE_RE = re.compile(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})")
LINE_RE = re.compile(
    r"^\s*(?P<label>[^\s:：（()）]{1,12})\s*"
    r"(?:[（(](?P<en>[A-Za-z][A-Za-z /\-]{1,24})[)）])?"
    r"\s*[:：]\s*(?P<value>.+?)\s*$"
)

LABEL_MAP = {
    "授权方": "licensor",
    "licensor": "licensor",
    "被授权方": "licensee",
    "licensee": "licensee",
    "授权类型": "scope_type",
    "授权类别": "scope_type",
    "scope": "scope_type",
    "scope type": "scope_type",
    "授权范围": "scope_items",
    "授权内容": "scope_items",
    "scope items": "scope_items",
    "地域范围": "territory",
    "授权地域": "territory",
    "地域": "territory",
    "territory": "territory",
    "媒介范围": "media",
    "授权媒介": "media",
    "媒介": "media",
    "media": "media",
    "生效日期": "start",
    "起始日期": "start",
    "start": "start",
    "start date": "start",
    "到期日期": "end",
    "终止日期": "end",
    "end": "end",
    "end date": "end",
    "可转授权": "sublicensable",
    "sublicensable": "sublicensable",
    "限制条款": "restrictions",
    "限制": "restrictions",
    "restrictions": "restrictions",
}

LIST_FIELDS = {"scope_items", "territory", "media", "restrictions"}


@dataclass
class TextResult:
    text: str
    source: str  # pdf | pdf_ocr | docx | image | none
    confidence: float
    warnings: list[str] = field(default_factory=list)


@dataclass
class LicenseFields:
    licensor: str = ""
    licensee: str = ""
    scope_type: str = ""
    scope_items: list[str] = field(default_factory=list)
    territory: list[str] = field(default_factory=list)
    media: list[str] = field(default_factory=list)
    start: date | None = None
    end: date | None = None
    sublicensable: bool | None = None
    restrictions: list[str] = field(default_factory=list)
    field_confidence: dict[str, float] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)

    def as_confidence(self) -> dict[str, float]:
        return dict(self.field_confidence)


def _label_key(label: str | None, english: str | None) -> str | None:
    for candidate in (label, english):
        if not candidate:
            continue
        key = " ".join(candidate.strip().casefold().split())
        if key in LABEL_MAP:
            return LABEL_MAP[key]
    return None


def _split_list(value: str) -> list[str]:
    return [part.strip() for part in LIST_SPLIT_RE.split(value) if part.strip()]


def parse_date(value: str | None) -> date | None:
    match = DATE_RE.search(value or "")
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def parse_bool(value: str | None) -> bool | None:
    key = (value or "").strip().casefold()
    if key in {"是", "可", "可以", "true", "yes", "y", "1"}:
        return True
    if key in {"否", "不可", "不能", "false", "no", "n", "0"}:
        return False
    return None


def _canonical_scope(value: str) -> str:
    key = value.strip().casefold()
    return license_status.SCOPE_ALIASES.get(key, key)


def parse_fields(text: str, base_confidence: float = PDF_TEXT) -> LicenseFields:
    fields = LicenseFields()
    found: set[str] = set()

    for raw_line in (text or "").splitlines():
        match = LINE_RE.match(raw_line)
        if not match:
            continue
        key = _label_key(match.group("label"), match.group("en"))
        if key is None or key in found:
            continue
        value = match.group("value").strip()
        if not value:
            continue

        if key in LIST_FIELDS:
            setattr(fields, key, _split_list(value))
        elif key in ("start", "end"):
            parsed = parse_date(value)
            if parsed is None:
                continue
            setattr(fields, key, parsed)
        elif key == "sublicensable":
            parsed_bool = parse_bool(value)
            if parsed_bool is None:
                continue
            fields.sublicensable = parsed_bool
        elif key == "scope_type":
            fields.scope_type = _canonical_scope(value)
        else:
            setattr(fields, key, value)
        found.add(key)
        fields.field_confidence[key] = base_confidence

    fields.missing_fields = [
        name for name in license_status.KEY_FIELDS if not _has_value(fields, name)
    ]
    return fields


def _has_value(fields: LicenseFields, name: str) -> bool:
    value = getattr(fields, name, None)
    if isinstance(value, list):
        return bool(value)
    return value not in (None, "")


def _pdf_text(path: Path) -> tuple[str, list[str]]:
    try:
        import pymupdf
    except ImportError:
        return "", ["未安装 pymupdf，无法读取 PDF 文本层"]
    try:
        with pymupdf.open(path) as doc:
            return "\n".join(page.get_text("text") for page in doc), []
    except Exception as exc:
        return "", [f"PDF 解析失败: {exc}"]


def _pdf_ocr_text(path: Path) -> tuple[str, list[str]]:
    try:
        import pymupdf
    except ImportError:
        return "", ["未安装 pymupdf"]
    from app.pipeline import ocr

    texts: list[str] = []
    warnings: list[str] = []
    try:
        with pymupdf.open(path) as doc:
            for page in doc:
                pixmap = page.get_pixmap(dpi=200)
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
                    image_path = Path(handle.name)
                try:
                    pixmap.save(image_path)
                    result = ocr.ocr_image(image_path)
                    if result.status != "ok":
                        warnings.append(f"扫描件 OCR 不可用: {result.error or result.status}")
                        continue
                    texts.append(result.text)
                finally:
                    image_path.unlink(missing_ok=True)
    except Exception as exc:
        warnings.append(f"PDF 渲染失败: {exc}")
    return "\n".join(texts), warnings


def _docx_text(path: Path) -> tuple[str, list[str]]:
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    except Exception as exc:
        return "", [f"DOCX 解析失败: {exc}"]
    xml = xml.replace("</w:p>", "\n")
    text = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml, flags=re.S))
    return re.sub(r"<[^>]+>", "", text), []


def _image_text(path: Path) -> tuple[str, list[str]]:
    from app.pipeline import ocr

    result = ocr.ocr_image(path)
    if result.status != "ok":
        return "", [f"OCR 不可用: {result.error or result.status}"]
    return result.text, []


def extract_text(path: Path) -> TextResult:
    suffix = path.suffix.casefold()
    if suffix == ".pdf":
        text, warnings = _pdf_text(path)
        if len(text.strip()) >= 20:
            return TextResult(text, "pdf", PDF_TEXT, warnings)
        ocr_text, ocr_warnings = _pdf_ocr_text(path)
        if ocr_text.strip():
            return TextResult(ocr_text, "pdf_ocr", IMAGE_TEXT, [*warnings, *ocr_warnings])
        return TextResult("", "none", 0.0, [*warnings, *ocr_warnings, "PDF 无可用文本"])
    if suffix == ".docx":
        text, warnings = _docx_text(path)
        return TextResult(text, "docx", DOCX_TEXT if text.strip() else 0.0, warnings)
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        text, warnings = _image_text(path)
        return TextResult(text, "image", IMAGE_TEXT if text.strip() else 0.0, warnings)
    return TextResult("", "none", 0.0, [f"不支持的授权文件类型: {suffix or '无扩展名'}"])


def _llm_extract(text: str) -> LicenseFields | None:
    """可选：LLM 结构化抽取补空字段。未配置或失败时返回 None，主路径不受影响。"""
    if not settings.ledger_llm_enabled:
        return None
    try:
        import anthropic
        from pydantic import BaseModel
    except ImportError:
        logger.warning("未安装 anthropic，跳过 LLM 抽取")
        return None

    class Payload(BaseModel):
        licensor: str | None = None
        licensee: str | None = None
        scope_type: str | None = None
        scope_items: list[str] = []
        territory: list[str] = []
        media: list[str] = []
        start: str | None = None
        end: str | None = None
        sublicensable: bool | None = None
        restrictions: list[str] = []

    system = (
        "你是授权书字段抽取助手，只做事实抽取，不推断未写明的内容；"
        "日期统一输出 YYYY-MM-DD，不确定的字段输出 null。"
    )
    try:
        client = anthropic.Anthropic()
        response = client.messages.parse(
            model=settings.ledger_llm_model,
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": text[:20000]}],
            output_format=Payload,
            thinking={"type": "adaptive"},
        )
        payload = response.parsed_output
    except Exception as exc:
        logger.warning("LLM 授权抽取失败，回退规则解析: %s", exc)
        return None

    return LicenseFields(
        licensor=payload.licensor or "",
        licensee=payload.licensee or "",
        scope_type=_canonical_scope(payload.scope_type or ""),
        scope_items=[item for item in payload.scope_items if item],
        territory=[item for item in payload.territory if item],
        media=[item for item in payload.media if item],
        start=parse_date(payload.start),
        end=parse_date(payload.end),
        sublicensable=payload.sublicensable,
        restrictions=[item for item in payload.restrictions if item],
        field_confidence={},
        missing_fields=[],
    )


def _merge(rule_fields: LicenseFields, llm_fields: LicenseFields | None) -> LicenseFields:
    if llm_fields is None:
        return rule_fields
    merged = LicenseFields(**{**rule_fields.__dict__})
    for name in (
        "licensor",
        "licensee",
        "scope_type",
        "scope_items",
        "territory",
        "media",
        "start",
        "end",
        "sublicensable",
        "restrictions",
    ):
        if not _has_value(merged, name) and _has_value(llm_fields, name):
            setattr(merged, name, getattr(llm_fields, name))
            merged.field_confidence[name] = 0.8
    merged.missing_fields = [
        name for name in license_status.KEY_FIELDS if not _has_value(merged, name)
    ]
    return merged


@dataclass
class LicenseExtraction:
    fields: LicenseFields
    text: TextResult

    @property
    def warnings(self) -> list[str]:
        return self.text.warnings


def extract_license(path: Path) -> LicenseExtraction:
    text = extract_text(path)
    fields = parse_fields(text.text, base_confidence=text.confidence or PDF_TEXT)
    fields = _merge(fields, _llm_extract(text.text) if text.text.strip() else None)
    return LicenseExtraction(fields=fields, text=text)


def build_record(extraction: LicenseExtraction, source_doc: str) -> LicenseRecord:
    fields = extraction.fields
    record = LicenseRecord(
        licensor=fields.licensor,
        licensee=fields.licensee,
        scope_type=fields.scope_type,
        scope_items=fields.scope_items or None,
        territory=fields.territory or None,
        media=fields.media or None,
        start=fields.start,
        end=fields.end,
        sublicensable=fields.sublicensable,
        restrictions=fields.restrictions or None,
        source_doc=source_doc,
        field_confidence=fields.as_confidence(),
    )
    record.status = license_status.compute_status(record).status
    return record
