from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .editorial import EditorialValidationError, validate_external_editorial
from .models import EditorialAudit, EditorialCandidate, EditorialPlan

CHAT_BRIDGE_SCHEMA_VERSION = 1
QUEUE_ID_PATTERN = re.compile(r"[0-9a-f]{64}")
DRAFT_ID_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{8,128}")


class ChatEditorialQueuePart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = CHAT_BRIDGE_SCHEMA_VERSION
    queue_id: str
    part_index: int = Field(ge=1)
    part_count: int = Field(ge=1)
    candidates: list[EditorialCandidate]

    @field_validator("queue_id")
    @classmethod
    def validate_queue_id(cls, value: str) -> str:
        if not QUEUE_ID_PATTERN.fullmatch(value):
            raise ValueError("queue_id must be a lowercase SHA-256 hex digest")
        return value

    @model_validator(mode="after")
    def validate_part_index(self) -> ChatEditorialQueuePart:
        if self.part_index > self.part_count:
            raise ValueError("part_index exceeds part_count")
        if not self.candidates:
            raise ValueError("queue part must contain at least one candidate")
        return self


class ChatEditorialQueueManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = CHAT_BRIDGE_SCHEMA_VERSION
    report_date: str
    queue_id: str
    generated_at: datetime
    report_start: datetime
    report_end: datetime
    initial_health_finished_at: datetime
    candidate_count: int = Field(ge=1)
    part_count: int = Field(ge=1)
    gap_detection_status: Literal["complete", "degraded", "failed"]
    gap_detection_route: str
    gap_queries_attempted: int = Field(ge=0)
    gap_queries_completed: int = Field(ge=0)
    gap_potential_count: int = Field(ge=0)

    @field_validator("queue_id")
    @classmethod
    def validate_queue_id(cls, value: str) -> str:
        if not QUEUE_ID_PATTERN.fullmatch(value):
            raise ValueError("queue_id must be a lowercase SHA-256 hex digest")
        return value

    @field_validator("generated_at", "report_start", "report_end", "initial_health_finished_at")
    @classmethod
    def normalize_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_manifest(self) -> ChatEditorialQueueManifest:
        try:
            parsed_date = datetime.strptime(self.report_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("report_date must be YYYY-MM-DD") from exc
        if parsed_date.isoformat() != self.report_date:
            raise ValueError("report_date must be canonical YYYY-MM-DD")
        if self.report_start >= self.report_end:
            raise ValueError("report_start must precede report_end")
        if self.initial_health_finished_at > self.generated_at:
            raise ValueError("queue cannot predate its initial collection health")
        if self.gap_queries_completed > self.gap_queries_attempted:
            raise ValueError("completed gap queries exceed attempted queries")
        return self


class ChatEditorialQueue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: ChatEditorialQueueManifest
    candidates: list[EditorialCandidate]

    @model_validator(mode="after")
    def validate_candidates(self) -> ChatEditorialQueue:
        candidate_ids = [candidate.candidate_id for candidate in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("queue contains duplicate candidate_id values")
        if len(candidate_ids) != self.manifest.candidate_count:
            raise ValueError("queue candidate count does not match manifest")
        if editorial_queue_id(self.candidates) != self.manifest.queue_id:
            raise ValueError("queue candidate digest does not match manifest queue_id")
        return self


class ChatEditorialDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1] = CHAT_BRIDGE_SCHEMA_VERSION
    report_date: str
    queue_id: str
    draft_id: str
    submitted_at: datetime
    plan: EditorialPlan

    @field_validator("queue_id")
    @classmethod
    def validate_queue_id(cls, value: str) -> str:
        if not QUEUE_ID_PATTERN.fullmatch(value):
            raise ValueError("queue_id must be a lowercase SHA-256 hex digest")
        return value

    @field_validator("draft_id")
    @classmethod
    def validate_draft_id(cls, value: str) -> str:
        if not DRAFT_ID_PATTERN.fullmatch(value):
            raise ValueError("draft_id contains unsupported characters or length")
        return value

    @field_validator("submitted_at")
    @classmethod
    def normalize_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("submitted_at must be timezone-aware")
        return value.astimezone(UTC)


class ChatEditorialAuditSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1] = CHAT_BRIDGE_SCHEMA_VERSION
    report_date: str
    queue_id: str
    draft_id: str
    submitted_at: datetime
    audit: EditorialAudit

    @field_validator("queue_id")
    @classmethod
    def validate_queue_id(cls, value: str) -> str:
        if not QUEUE_ID_PATTERN.fullmatch(value):
            raise ValueError("queue_id must be a lowercase SHA-256 hex digest")
        return value

    @field_validator("draft_id")
    @classmethod
    def validate_draft_id(cls, value: str) -> str:
        if not DRAFT_ID_PATTERN.fullmatch(value):
            raise ValueError("draft_id contains unsupported characters or length")
        return value

    @field_validator("submitted_at")
    @classmethod
    def normalize_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("submitted_at must be timezone-aware")
        return value.astimezone(UTC)


class ChatEditorialBridgeBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queue: ChatEditorialQueue
    draft: ChatEditorialDraft
    audit: ChatEditorialAuditSubmission


def bounded_queue_candidate(
    candidate: EditorialCandidate, evidence_chars: int
) -> EditorialCandidate:
    summary = candidate.summary
    return candidate.model_copy(
        update={
            "summary": summary[:evidence_chars] if summary else None,
            "evidence_text": candidate.evidence_text[:evidence_chars],
        }
    )


def editorial_queue_id(candidates: list[EditorialCandidate]) -> str:
    payload = [
        candidate.model_dump(mode="json")
        for candidate in sorted(candidates, key=lambda item: item.candidate_id)
    ]
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def validate_chat_editorial_bridge(bundle: ChatEditorialBridgeBundle) -> None:
    manifest = bundle.queue.manifest
    draft = bundle.draft
    audit = bundle.audit
    errors: list[str] = []
    if draft.report_date != manifest.report_date or audit.report_date != manifest.report_date:
        errors.append("queue, draft, and audit report_date values do not match")
    if draft.queue_id != manifest.queue_id or audit.queue_id != manifest.queue_id:
        errors.append("queue, draft, and audit queue_id values do not match")
    if audit.draft_id != draft.draft_id:
        errors.append("audit draft_id does not match the submitted draft")
    if draft.submitted_at < manifest.generated_at:
        errors.append("draft predates the verified editorial queue")
    if audit.submitted_at < draft.submitted_at:
        errors.append("audit predates the editorial draft")
    if errors:
        raise EditorialValidationError("ChatGPT bridge validation failed: " + "; ".join(errors))
    validate_external_editorial(
        plan=draft.plan,
        audit=audit.audit,
        candidates=bundle.queue.candidates,
    )
