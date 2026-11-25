"""授权状态机单测：PROMPT §9 的七种状态与降级规则。"""

from datetime import date, timedelta
from types import SimpleNamespace

from app.ledger import status
from app.models import LicenseStatus

TODAY = date(2026, 11, 25)


def record(**overrides):
    base = {
        "scope_type": "music",
        "territory": ["东南亚", "美国"],
        "media": ["TikTok", "Meta"],
        "start": date(2026, 1, 1),
        "end": date(2027, 12, 31),
        "field_confidence": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def campaign(**overrides):
    base = {"platform": "tiktok", "market": "SEA", "start": date(2026, 12, 1), "end": None}
    base.update(overrides)
    return SimpleNamespace(**base)


def test_effective_when_scope_and_dates_match():
    result = status.compute_status(record(), campaign(), today=TODAY)
    assert result.status == LicenseStatus.effective.value
    assert result.campaign_missing is False
    assert result.reasons == []


def test_expired_beats_other_checks():
    result = status.compute_status(
        record(end=date(2025, 6, 30)), campaign(market="欧盟"), today=TODAY
    )
    assert result.status == LicenseStatus.expired.value


def test_expired_when_campaign_starts_after_licence_end():
    result = status.compute_status(
        record(end=date(2026, 12, 1)),
        campaign(start=date(2027, 1, 1)),
        today=TODAY,
    )
    assert result.status == LicenseStatus.expired.value
    assert "投放开始于" in result.reasons[0]


def test_expiring_soon_within_30_days():
    result = status.compute_status(
        record(end=TODAY + timedelta(days=10)), campaign(), today=TODAY
    )
    assert result.status == LicenseStatus.expiring_soon.value


def test_out_of_territory_for_sea_campaign():
    result = status.compute_status(record(territory=["美国", "欧盟"]), campaign(), today=TODAY)
    assert result.status == LicenseStatus.out_of_territory.value
    assert result.is_risk


def test_territory_alias_matches_country_and_region():
    assert status.market_in_territory(["新加坡"], "SEA") is True
    assert status.market_in_territory(["全球"], "SEA") is True
    assert status.market_in_territory(["欧洲"], "美国") is False
    assert status.market_in_territory(["欧盟"], "") is None


def test_out_of_media_when_platform_not_licensed():
    result = status.compute_status(record(media=["Meta"]), campaign(platform="tiktok"), today=TODAY)
    assert result.status == LicenseStatus.out_of_media.value


def test_out_of_scope_when_license_type_does_not_cover_category():
    result = status.compute_status(record(), campaign(), category="logo", today=TODAY)
    assert result.status == LicenseStatus.out_of_scope.value


def test_needs_review_when_key_field_missing():
    result = status.compute_status(record(end=None), campaign(), today=TODAY)
    assert result.status == LicenseStatus.needs_review.value
    assert any("到期日期" in reason for reason in result.reasons)


def test_needs_review_when_confidence_below_threshold():
    result = status.compute_status(
        record(field_confidence={"territory": 0.5}), campaign(), today=TODAY
    )
    assert result.status == LicenseStatus.needs_review.value
    assert any("地域范围" in reason for reason in result.reasons)


def test_campaign_missing_blocks_territory_judgement():
    result = status.compute_status(record(), campaign=None, today=TODAY)
    assert result.status == LicenseStatus.effective.value
    assert result.campaign_missing is True
    assert any("无法核验" in reason for reason in result.reasons)


def test_covers_category_mapping():
    assert status.covers_category("music", "bgm") is True
    assert status.covers_category("music", "logo") is False
    assert status.covers_category("unknown-scope", "logo") is None
