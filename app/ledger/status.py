"""授权状态机（PROMPT §9）。

关键约定：

- 关键字段（scope_type / territory / start / end）缺失或置信 < 0.7 → needs_review，
  不参与判定，也不默认通过或拒绝；
- 没有 Campaign 时地域与媒介「无法核验」：状态只按日期给出，并把 campaign_missing
  交给调用方去提示「需补充投放信息」（P2）；
- 判定优先级：needs_review > out_of_scope > expired > out_of_territory >
  out_of_media > expiring_soon > effective。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.models import LicenseStatus

KEY_FIELDS = ("scope_type", "territory", "start", "end")
CONFIDENCE_MIN = 0.7
EXPIRING_DAYS = 30

FIELD_LABELS = {
    "scope_type": "授权类型",
    "territory": "地域范围",
    "start": "生效日期",
    "end": "到期日期",
}

GLOBAL_TERMS = frozenset(
    {"global", "worldwide", "world", "world-wide", "全球", "全球范围", "不限", "无限制"}
)

MARKET_ALIASES: dict[str, frozenset[str]] = {
    "sea": frozenset(
        {
            "sea",
            "southeast asia",
            "south-east asia",
            "东盟",
            "东南亚",
            "新加坡",
            "泰国",
            "越南",
            "印度尼西亚",
            "印尼",
            "马来西亚",
            "菲律宾",
            "sg",
            "th",
            "vn",
            "id",
            "my",
            "ph",
        }
    ),
    "us": frozenset(
        {"us", "usa", "united states", "united states of america", "north america", "美国", "北美"}
    ),
    "eu": frozenset({"eu", "european union", "europe", "欧盟", "欧洲"}),
    "uk": frozenset({"uk", "gb", "united kingdom", "britain", "英国"}),
    "jp": frozenset({"jp", "japan", "日本"}),
    "kr": frozenset({"kr", "korea", "south korea", "韩国"}),
}

PLATFORM_ALIASES: dict[str, frozenset[str]] = {
    "tiktok": frozenset({"tiktok", "tik tok", "tt"}),
    "meta": frozenset({"meta", "facebook", "instagram", "fb", "ig"}),
    "google": frozenset({"google", "youtube", "yt"}),
}

SCOPE_CATEGORIES: dict[str, frozenset[str]] = {
    "music": frozenset({"bgm"}),
    "image": frozenset({"copyright_image"}),
    "trademark": frozenset({"logo", "trademark_text"}),
    "character": frozenset({"celebrity"}),
    "font": frozenset({"font"}),
}

SCOPE_ALIASES = {
    "音乐": "music",
    "音频": "music",
    "audio": "music",
    "图片": "image",
    "图像": "image",
    "商标": "trademark",
    "角色": "character",
    "字体": "font",
}


@dataclass
class StatusResult:
    status: str
    reasons: list[str] = field(default_factory=list)
    campaign_missing: bool = False

    @property
    def is_risk(self) -> bool:
        return self.status in {
            LicenseStatus.expired.value,
            LicenseStatus.out_of_territory.value,
            LicenseStatus.out_of_media.value,
            LicenseStatus.out_of_scope.value,
        }


def _norm(value) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def market_key(value: str | None) -> str | None:
    key = _norm(value)
    if not key:
        return None
    if key in GLOBAL_TERMS:
        return "global"
    for canonical, aliases in MARKET_ALIASES.items():
        if key == canonical or key in aliases:
            return canonical
    return key


def market_in_territory(territory: list | None, market: str | None) -> bool | None:
    """返回 True/False；market 为空或无法识别时返回 None（无法核验）。"""
    wanted = market_key(market)
    if wanted is None:
        return None
    for item in territory or []:
        if market_key(item) in ("global", wanted):
            return True
    return False


def _platform_keys(value: str | None) -> set[str]:
    key = _norm(value)
    if not key:
        return set()
    keys = {key}
    for canonical, aliases in PLATFORM_ALIASES.items():
        if key == canonical or key in aliases:
            keys.add(canonical)
    return keys


def platform_in_media(media: list | None, platform: str | None) -> bool | None:
    if not _norm(platform):
        return None
    wanted = _platform_keys(platform)
    for item in media or []:
        if _platform_keys(item) & wanted:
            return True
    return False


def covers_category(scope_type: str | None, category: str | None) -> bool | None:
    """授权类型能否覆盖某类风险信号；未知类型返回 None（不据此定级）。"""
    scope = _norm(scope_type)
    scope = SCOPE_ALIASES.get(scope, scope)
    categories = SCOPE_CATEGORIES.get(scope)
    if categories is None or category is None:
        return None
    return category in categories


def _present(record, name: str) -> bool:
    value = getattr(record, name, None)
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return value not in (None, "")


def compute_status(
    record,
    campaign=None,
    category: str | None = None,
    today: date | None = None,
) -> StatusResult:
    today = today or date.today()
    campaign_missing = campaign is None

    def result(status: LicenseStatus, reasons: list[str]) -> StatusResult:
        return StatusResult(status.value, reasons, campaign_missing)

    confidence = record.field_confidence or {}
    missing = [name for name in KEY_FIELDS if not _present(record, name)]
    weak = [
        name
        for name in KEY_FIELDS
        if isinstance(confidence.get(name), (int, float))
        and confidence[name] < CONFIDENCE_MIN
    ]
    if missing or weak:
        reasons = []
        if missing:
            reasons.append("缺少 " + "、".join(FIELD_LABELS[name] for name in missing))
        if weak:
            reasons.append("置信不足 " + "、".join(FIELD_LABELS[name] for name in weak))
        return result(LicenseStatus.needs_review, reasons)

    if category is not None and covers_category(record.scope_type, category) is False:
        return result(
            LicenseStatus.out_of_scope,
            [f"授权类型「{record.scope_type}」不覆盖风险类别「{category}」"],
        )

    if record.end is not None and record.end < today:
        return result(LicenseStatus.expired, [f"到期日 {record.end.isoformat()} 已过期"])

    campaign_start = getattr(campaign, "start", None)
    if campaign is not None and campaign_start and record.end and record.end < campaign_start:
        return result(
            LicenseStatus.expired,
            [
                f"投放开始于 {campaign_start.isoformat()}，"
                f"此前授权已到期（{record.end.isoformat()}）"
            ],
        )

    reasons: list[str] = []
    if campaign is None:
        reasons.append("缺少投放信息（platform/market），地域与媒介无法核验")
    else:
        if record.territory:
            covered = market_in_territory(record.territory, campaign.market)
            if covered is False:
                return result(
                    LicenseStatus.out_of_territory,
                    [
                        f"授权地域 {'、'.join(str(t) for t in record.territory)} "
                        f"不含投放市场「{campaign.market or '未填写'}」"
                    ],
                )
            if covered is None:
                reasons.append("投放市场未识别，地域范围需人工核验")
        if record.media:
            in_media = platform_in_media(record.media, campaign.platform)
            if in_media is False:
                return result(
                    LicenseStatus.out_of_media,
                    [
                        f"授权媒介 {'、'.join(str(m) for m in record.media)} "
                        f"不含投放平台「{campaign.platform or '未填写'}」"
                    ],
                )

    if record.end is not None and record.end <= today + timedelta(days=EXPIRING_DAYS):
        return result(
            LicenseStatus.expiring_soon,
            [*reasons, f"到期日 {record.end.isoformat()} 在 {EXPIRING_DAYS} 天内"],
        )
    return result(LicenseStatus.effective, reasons)
