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


class VerificationGrade(StrEnum):
    """Evidence strength, never a substitute for the underlying status fields."""

    A = "A"
    B = "B"
    C = "C"
    D = "D"


class PrimarySourceValidation(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    VERIFIED = "verified"
    UNAVAILABLE = "unavailable"
    MISMATCH = "mismatch"


class DiscoveryStatus(StrEnum):
    PENDING = "pending"
    COMPLETE = "complete"
    PARTIAL = "partial"
    CONFIGURATION_MISSING = "configuration_missing"
    QUOTA_EXCEEDED = "quota_exceeded"
    UNAVAILABLE = "unavailable"


class ArticleDiscovery(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    source: str
    article_id: str | None = None
    canonical_url: str
    title: str
    byline: str | None = None
    section: str | None = None
    published_at: datetime | None = None
    updated_at: datetime | None = None
    summary: str | None = None
    refresh_only: bool = False
    discovery_route: list[str] = Field(default_factory=list)

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
    byline: str | None = None
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
    verification_grade: VerificationGrade = VerificationGrade.D
    discovery_route: list[str] = Field(default_factory=list)
    issue_id: str | None = None
    primary_source_validation: PrimarySourceValidation = PrimarySourceValidation.NOT_REQUIRED
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
        if self.verification_status == VerificationStatus.BODY_VERIFIED:
            self.verification_grade = VerificationGrade.A
        elif (
            self.verification_status == VerificationStatus.METADATA_ONLY
            and self.published_at is not None
            and self.canonical_url
        ):
            self.verification_grade = VerificationGrade.B
        elif self.discovery_route and any(
            route.startswith(("naver_api:", "external_search:")) for route in self.discovery_route
        ):
            self.verification_grade = VerificationGrade.C
        return self


class DiscoveryPage(BaseModel):
    articles: list[ArticleDiscovery] = Field(default_factory=list)
    child_urls: list[str] = Field(default_factory=list)
    removed_article_ids: list[str] = Field(default_factory=list)
    stop_pagination: bool = False


class SourceHealth(BaseModel):
    source: str
    success: bool = False
    discovery_status: DiscoveryStatus = DiscoveryStatus.PENDING
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
    refreshed: int = 0
    removed: int = 0
    errors: list[str] = Field(default_factory=list)
    structure_warnings: list[str] = Field(default_factory=list)
    # Discovery-only contract warnings are separated from body-parser warnings so
    # a census is decided by the completeness of the official list, not by an
    # unrelated article-body selector failure.
    discovery_warnings: list[str] = Field(default_factory=list)
    discovery_paths_attempted: int = 0
    discovery_paths_succeeded: int = 0
    oldest_discovered_at: datetime | None = None
    newest_discovered_at: datetime | None = None
    unclassified_failures: int = 0


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


class EditorialSection(StrEnum):
    DISABILITY = "disability"
    LABOR = "labor"
    OPINION = "opinion"


class EditorialVerdict(StrEnum):
    INCLUDE = "include"
    EXCLUDE = "exclude"
    REVIEW = "review"


class EditorialCandidate(BaseModel):
    """Ephemeral evidence supplied to the editorial model, never repository data."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    candidate_id: str
    source: str
    canonical_url: str
    title: str
    byline: str | None
    section: str | None
    published_at: datetime | None
    summary: str | None
    evidence_text: str
    body_status: BodyStatus
    verification_status: VerificationStatus
    rule_classification: Classification
    rule_score: float
    primary_source_validation: PrimarySourceValidation = PrimarySourceValidation.NOT_REQUIRED

    @property
    def selectable(self) -> bool:
        if self.verification_status == VerificationStatus.BODY_VERIFIED:
            return len(self.evidence_text.strip()) >= 80
        return (
            self.body_status == BodyStatus.NOT_REQUESTED
            and len((self.summary or self.evidence_text).strip()) >= 80
        )


class EditorialAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    candidate_id: str
    verdict: EditorialVerdict
    section: EditorialSection | None
    issue_label: str
    importance: int
    reason: str

    @model_validator(mode="after")
    def included_candidate_requires_section(self) -> EditorialAssessment:
        if self.verdict == EditorialVerdict.INCLUDE and self.section is None:
            raise ValueError("included editorial assessment requires a section")
        return self


class EditorialAssessmentBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessments: list[EditorialAssessment]


class EditorialIssueDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    section: EditorialSection
    title: str
    candidate_ids: list[str]
    summary: str
    tone_analysis: str


class EditorialExclusion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    candidate_id: str
    reason: str


class EditorialPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issues: list[EditorialIssueDecision]
    exclusions: list[EditorialExclusion]


class AuditSeverity(StrEnum):
    FATAL = "fatal"
    WARNING = "warning"


class EditorialAuditFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    severity: AuditSeverity
    issue_title: str | None
    candidate_ids: list[str]
    code: str
    explanation: str


class EditorialAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[EditorialAuditFinding]
    progressive_issue_titles: list[str]

    @property
    def fatal_error_count(self) -> int:
        return sum(finding.severity == AuditSeverity.FATAL for finding in self.findings)
