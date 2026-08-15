from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Classification(StrEnum):
    RELEVANT = "relevant"
    REVIEW = "review"
    IRRELEVANT = "irrelevant"


class BodyStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    SKIPPED_IRRELEVANT = "skipped_irrelevant"
    FETCHED = "fetched"
    BLOCKED_BY_ROBOTS = "blocked_by_robots"
    ROBOTS_UNAVAILABLE = "robots_unavailable"
    HTTP_ERROR = "http_error"
    PARSE_ERROR = "parse_error"


class VerificationStatus(StrEnum):
    METADATA_ONLY = "metadata_only"
    BODY_VERIFIED = "body_verified"
    ROBOTS_BLOCKED = "robots_blocked"
    EXTRACTION_FAILED = "extraction_failed"


class ArticleDiscovery(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    source: str
    article_id: str | None = None
    canonical_url: str
    title: str
    section: str | None = None
    published_at: datetime | None = None
    updated_at: datetime | None = None
    summary: str | None = None

    @field_validator("canonical_url", "title")
    @classmethod
    def reject_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("published_at", "updated_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        normalized = value.astimezone(UTC)
        if normalized > datetime.now(UTC) + timedelta(hours=6):
            raise ValueError("future datetime rejected")
        return normalized


class ClassificationResult(BaseModel):
    topic: str = "disability_rights"
    classification: Classification
    topic_score: float
    matched_terms: list[str] = Field(default_factory=list)
    excluded_terms: list[str] = Field(default_factory=list)
    classification_reason: str
    candidate: bool


class ArticleRecord(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    source: str
    article_id: str | None = None
    canonical_url: str
    title: str
    section: str | None = None
    published_at: datetime | None = None
    updated_at: datetime | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    summary: str | None = None
    monitor_summary: str | None = None
    body_status: BodyStatus = BodyStatus.NOT_REQUESTED
    content_hash: str | None = None
    classification: Classification
    topic_score: float
    matched_terms: list[str] = Field(default_factory=list)
    excluded_terms: list[str] = Field(default_factory=list)
    classification_reason: str
    verification_status: VerificationStatus = VerificationStatus.METADATA_ONLY
    collection_error: str | None = None

    @field_validator("published_at", "updated_at", "first_seen_at", "last_seen_at")
    @classmethod
    def normalize_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_seen_order(self) -> ArticleRecord:
        if self.last_seen_at < self.first_seen_at:
            raise ValueError("last_seen_at precedes first_seen_at")
        return self


class DiscoveryPage(BaseModel):
    articles: list[ArticleDiscovery] = Field(default_factory=list)
    child_urls: list[str] = Field(default_factory=list)
    stop_pagination: bool = False


class SourceHealth(BaseModel):
    source: str
    success: bool = False
    started_at: datetime
    finished_at: datetime | None = None
    discovered: int = 0
    in_window: int = 0
    new: int = 0
    duplicates: int = 0
    updated: int = 0
    relevant: int = 0
    review: int = 0
    irrelevant: int = 0
    bodies_checked: int = 0
    bodies_blocked: int = 0
    errors: list[str] = Field(default_factory=list)
    structure_warnings: list[str] = Field(default_factory=list)


class RunHealth(BaseModel):
    run_started_at: datetime
    run_finished_at: datetime
    window_start: datetime
    window_end: datetime
    all_sources_failed: bool
    sources: dict[str, SourceHealth]


class StoreResult(StrEnum):
    NEW = "new"
    UPDATED = "updated"
    DUPLICATE = "duplicate"
