"""领域模型，字段与 PROMPT.md §6 保持一致；改字段须同步更新 PROMPT。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum

from sqlalchemy import JSON, Column, Text
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


class MediaType(StrEnum):
    image = "image"
    video = "video"


class Category(StrEnum):
    logo = "logo"
    copyright_image = "copyright_image"
    celebrity = "celebrity"
    bgm = "bgm"
    trademark_text = "trademark_text"
    policy = "policy"
    ai_generated = "ai_generated"
    font = "font"
    license = "license"


CATEGORY_LABELS: dict[str, str] = {
    Category.logo.value: "商标标识",
    Category.copyright_image.value: "版权图像",
    Category.celebrity.value: "公众人物",
    Category.bgm.value: "背景音乐",
    Category.trademark_text.value: "商标词与文案",
    Category.policy.value: "平台政策",
    Category.ai_generated.value: "AI 生成",
    Category.font.value: "字体",
    Category.license.value: "授权",
}


class Level(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


LEVEL_ORDER: dict[str, int] = {
    Level.P0.value: 0,
    Level.P1.value: 1,
    Level.P2.value: 2,
    Level.P3.value: 3,
}


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


class ReviewStatus(StrEnum):
    pending = "pending"
    confirmed = "confirmed"
    rejected = "rejected"


class EvidenceKind(StrEnum):
    reference = "reference"
    clause = "clause"
    bbox = "bbox"
    timestamp = "timestamp"
    metadata = "metadata"
    license = "license"


class ReviewActionType(StrEnum):
    confirm = "confirm"
    reject = "reject"


class LicenseStatus(StrEnum):
    effective = "effective"
    expiring_soon = "expiring_soon"
    expired = "expired"
    out_of_territory = "out_of_territory"
    out_of_media = "out_of_media"
    out_of_scope = "out_of_scope"
    needs_review = "needs_review"


class Asset(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    sha256: str = Field(index=True)
    media_type: str = Field(index=True)
    path: str
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    c2pa: dict | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime | None = None


class Job(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    asset_id: int = Field(foreign_key="asset.id", index=True)
    status: str = Field(default=JobStatus.queued.value, index=True)
    stage: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class Detection(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    asset_id: int = Field(foreign_key="asset.id", index=True)
    tool: str
    raw: dict | None = Field(default=None, sa_column=Column(JSON))
    confidence: float | None = None
    cost_usd: float = 0.0


class Finding(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    asset_id: int = Field(foreign_key="asset.id", index=True)
    category: str = Field(index=True)
    level: str = Field(index=True)
    confidence: float | None = None
    suggestion: str = Field(default="", sa_column=Column(Text))
    review_status: str = Field(default=ReviewStatus.pending.value, index=True)
    dedupe_key: str = Field(default="", index=True)


class Evidence(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    finding_id: int = Field(foreign_key="finding.id", index=True)
    kind: str = Field(index=True)
    ref_url: str | None = None
    ref_record_id: str | None = None
    bbox: str | None = None
    t_start: float | None = None
    t_end: float | None = None
    clause_id: str | None = None
    note: str | None = Field(default=None, sa_column=Column(Text))


class LicenseRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    licensor: str = ""
    licensee: str = ""
    scope_type: str = ""
    scope_items: list | None = Field(default=None, sa_column=Column(JSON))
    territory: list | None = Field(default=None, sa_column=Column(JSON))
    media: list | None = Field(default=None, sa_column=Column(JSON))
    start: date | None = None
    end: date | None = None
    sublicensable: bool | None = None
    restrictions: list | None = Field(default=None, sa_column=Column(JSON))
    source_doc: str = ""
    field_confidence: dict | None = Field(default=None, sa_column=Column(JSON))
    status: str = Field(default=LicenseStatus.needs_review.value, index=True)


class AssetLicense(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    asset_id: int = Field(foreign_key="asset.id", index=True)
    license_id: int = Field(foreign_key="licenserecord.id", index=True)
    linked_by: str = ""
    linked_at: datetime = Field(default_factory=utcnow)


class Campaign(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    asset_id: int = Field(foreign_key="asset.id", index=True)
    platform: str = ""
    market: str = ""
    start: date | None = None
    end: date | None = None


class PolicyClause(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    platform: str = Field(index=True)
    section: str = ""
    text: str = Field(default="", sa_column=Column(Text))
    effective_date: date | None = None
    url: str = ""


class ReviewAction(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    finding_id: int = Field(foreign_key="finding.id", index=True)
    reviewer: str = "web"
    action: str
    reason: str = Field(default="", sa_column=Column(Text))
    created_at: datetime = Field(default_factory=utcnow)


class FeishuEvent(SQLModel, table=True):
    """飞书消息事件：message_id 唯一，用来做幂等去重与「任务完成后回推卡片」。"""

    id: int | None = Field(default=None, primary_key=True)
    event_id: str = Field(default="", index=True)
    message_id: str = Field(default="", index=True, unique=True)
    chat_id: str = Field(default="", index=True)
    job_id: int | None = Field(default=None, foreign_key="job.id", index=True)
    status: str = Field(default="received", index=True)
    note: str = Field(default="", sa_column=Column(Text))
    created_at: datetime = Field(default_factory=utcnow)


class ToolCall(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    job_id: int | None = Field(default=None, foreign_key="job.id", index=True)
    tool: str = Field(index=True)
    input_hash: str = ""
    ok: bool = True
    latency_ms: int | None = None
    cost_usd: float = 0.0
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ToolCache(SQLModel, table=True):
    key: str = Field(primary_key=True)
    tool: str = Field(index=True)
    result_json: dict | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)
