from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .editorial import article_candidate_id
from .models import (
    ArticleRecord,
    AuditSeverity,
    EditorialAudit,
    EditorialCandidate,
    EditorialPlan,
    RunHealth,
    VerificationGrade,
    VerificationStatus,
)
from .policy import BriefingPolicy, SourceRegistry
from .sources import BROADCAST_SOURCES
from .storage import JsonlStorage


class CheckStatus(StrEnum):
    COMPLETE = "complete"
    DEGRADED = "degraded"
    FAILED = "failed"


class SearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str
    title: str
    original_url: str | None
    naver_url: str | None
    published_at: datetime | None
    description: str | None
    matched_source: str | None = None
    in_deterministic_collection: bool | None = None


class GapDetectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CheckStatus
    route: str
    queries_attempted: int
    queries_completed: int
    hits: list[SearchHit] = Field(default_factory=list)
    potential_gaps: list[SearchHit] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class CensusCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    status: CheckStatus
    reason: str
    discovered: int
    oldest_discovered_at: datetime | None
    discovery_paths_attempted: int
    discovery_paths_succeeded: int


class ReverseSourceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_title: str
    source: str
    status: CheckStatus
    match_count: int = 0
    reason: str


class ReverseSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CheckStatus
    checks: list[ReverseSourceCheck]


class FinalStateIssueCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_title: str
    status: CheckStatus
    progressive: bool
    changed_after_draft: bool = False
    reason: str
    evidence_urls: list[str] = Field(default_factory=list)


class FinalStateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CheckStatus
    checked_at: datetime
    checks: list[FinalStateIssueCheck]


class EvidenceArticle(BaseModel):
    """Persisted provenance only; article text is deliberately excluded."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    canonical_url: str
    title: str
    outlet: str
    reporter: str | None
    published_at: datetime | None
    modified_at: datetime | None
    discovered_at: datetime
    last_checked_at: datetime
    discovery_route: list[str]
    full_body_status: str
    body_hash: str | None
    verification_grade: VerificationGrade
    issue_id: str | None
    primary_source_validation: str


class EvidenceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    report_date: str
    generated_at: datetime
    articles: list[EvidenceArticle]
    census: list[CensusCheck]
    gap_detection: GapDetectionResult
    reverse_search: ReverseSearchResult
    final_state: FinalStateResult


class ReportingItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cause: str
    fallback: str
    result: str
    next_action: str


class PublishGateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    report_date: str
    checked_at: datetime
    allowed: bool
    fatal_errors: list[str]
    degraded_warnings: list[str]
    validator_fatal_errors: int
    unclassified_failures: int
    reporting_items: list[ReportingItem]


def evaluate_census(
    health: RunHealth,
    *,
    window_start: datetime,
    registry: SourceRegistry,
    policy: BriefingPolicy,
) -> list[CensusCheck]:
    checks: list[CensusCheck] = []
    for source in policy.publish_gate.disability_press_census_required:
        source_policy = registry.sources[source]
        detail = health.sources.get(source)
        if detail is None:
            checks.append(
                CensusCheck(
                    source=source,
                    status=CheckStatus.FAILED,
                    reason="수집 실행에 해당 출처 상태가 없음",
                    discovered=0,
                    oldest_discovered_at=None,
                    discovery_paths_attempted=0,
                    discovery_paths_succeeded=0,
                )
            )
            continue
        reason = "공식 목록 경로가 성공하고 조사 시작 경계 또는 목록 끝을 확인함"
        status = CheckStatus.COMPLETE
        census = source_policy.census
        if not detail.success:
            status = CheckStatus.FAILED
            reason = f"공식 목록 수집 실패: {detail.discovery_status.value}"
        elif detail.errors or detail.discovery_warnings:
            status = CheckStatus.DEGRADED
            reason = "공식 목록 일부 실패 또는 발견 구조 경고가 있음"
        elif census is None:
            status = CheckStatus.FAILED
            reason = "source-registry에 census 계약이 없음"
        elif detail.discovery_paths_succeeded != detail.discovery_paths_attempted:
            status = CheckStatus.DEGRADED
            reason = "시도한 공식 목록 경로 일부가 성공하지 않음"
        elif census.mode == "bounded_official_api":
            if detail.discovered >= census.page_size:
                status = CheckStatus.DEGRADED
                reason = "공식 API 결과가 페이지 상한에 도달해 다음 페이지 확인이 필요함"
        elif census.requires_window_boundary:
            exhausted = detail.discovered < census.page_size
            boundary_seen = (
                detail.oldest_discovered_at is not None
                and detail.oldest_discovered_at <= window_start
            )
            if not (exhausted or boundary_seen):
                status = CheckStatus.DEGRADED
                reason = "공식 최신 목록에서 조사 시작 경계까지 도달하지 못함"
        checks.append(
            CensusCheck(
                source=source,
                status=status,
                reason=reason,
                discovered=detail.discovered,
                oldest_discovered_at=detail.oldest_discovered_at,
                discovery_paths_attempted=detail.discovery_paths_attempted,
                discovery_paths_succeeded=detail.discovery_paths_succeeded,
            )
        )
    return checks


def build_evidence_manifest(
    *,
    report_date: str,
    articles: list[ArticleRecord],
    plan: EditorialPlan,
    census: list[CensusCheck],
    gap_detection: GapDetectionResult,
    reverse_search: ReverseSearchResult,
    final_state: FinalStateResult,
) -> EvidenceManifest:
    issue_by_candidate: dict[str, str] = {}
    for index, issue in enumerate(plan.issues, start=1):
        issue_id = f"{report_date}-issue-{index:02d}"
        for candidate_id in issue.candidate_ids:
            issue_by_candidate[candidate_id] = issue_id
    evidence: list[EvidenceArticle] = []
    for article in articles:
        candidate_id = article_candidate_id(article)
        evidence.append(
            EvidenceArticle(
                candidate_id=candidate_id,
                canonical_url=article.canonical_url,
                title=article.title,
                outlet=article.source,
                reporter=article.byline,
                published_at=article.published_at,
                modified_at=article.updated_at,
                discovered_at=article.first_seen_at,
                last_checked_at=article.last_seen_at,
                discovery_route=article.discovery_route,
                full_body_status=article.body_status.value,
                body_hash=article.content_hash,
                verification_grade=article.verification_grade,
                issue_id=issue_by_candidate.get(candidate_id),
                primary_source_validation=article.primary_source_validation.value,
            )
        )
    evidence.sort(key=lambda item: (item.published_at or item.discovered_at, item.outlet))
    return EvidenceManifest(
        report_date=report_date,
        generated_at=datetime.now(UTC),
        articles=evidence,
        census=census,
        gap_detection=gap_detection,
        reverse_search=reverse_search,
        final_state=final_state,
    )


def evaluate_publish_gate(
    *,
    report_date: str,
    policy: BriefingPolicy,
    census: list[CensusCheck],
    gap_detection: GapDetectionResult,
    reverse_search: ReverseSearchResult,
    final_state: FinalStateResult,
    audit: EditorialAudit,
    plan: EditorialPlan,
    candidates: list[EditorialCandidate],
    health: RunHealth,
) -> PublishGateDecision:
    fatal: list[str] = []
    warnings: list[str] = []
    reporting: list[ReportingItem] = []

    incomplete_census = [check for check in census if check.status != CheckStatus.COMPLETE]
    if incomplete_census:
        fatal.append("장애언론 census COMPLETE 조건을 충족하지 못함")
        for check in incomplete_census:
            reporting.append(
                ReportingItem(
                    cause=f"{check.source}: {check.reason}",
                    fallback="등록된 공식 목록 경로와 Naver gap detector를 분리 실행",
                    result=check.status.value,
                    next_action="공식 목록 경계를 재확인하고 COMPLETE 전에는 발행하지 않음",
                )
            )

    census_gap_hits = [
        hit
        for hit in gap_detection.potential_gaps
        if hit.matched_source in policy.publish_gate.disability_press_census_required
    ]
    if census_gap_hits:
        fatal.append("독립 gap detector가 장애언론 census와 모순되는 잠재 누락을 발견함")
        for hit in census_gap_hits:
            reporting.append(
                ReportingItem(
                    cause=f"{hit.matched_source}: 공식 목록에 없는 Naver 색인 후보 {hit.title}",
                    fallback="검색 색인을 원문 검증으로 승격하지 않고 후보 URL만 보존",
                    result="potential_gap",
                    next_action="공식 목록 경로에서 원문을 재발견·검증하고 census를 다시 판정",
                )
            )

    expected_reverse = {
        (issue.title, source)
        for issue in plan.issues
        for source in policy.publish_gate.designated_reverse_search_required
    }
    actual_reverse = {(check.issue_title, check.source) for check in reverse_search.checks}
    if actual_reverse != expected_reverse:
        fatal.append("지정매체 reverse-search 상태가 모든 이슈의 9/9를 포함하지 않음")
        reporting.append(
            ReportingItem(
                cause=(
                    "지정매체 reverse-search 상태 집합이 정책상 필요한 "
                    f"{len(expected_reverse)}개 조합과 일치하지 않음"
                ),
                fallback="수집된 공식 원문은 보존하되 누락된 상태를 COMPLETE로 추정하지 않음",
                result="failed",
                next_action="각 이슈별 지정 9개 매체 상태를 다시 생성한 뒤 gate 재실행",
            )
        )
    for check in reverse_search.checks:
        if check.status == CheckStatus.FAILED:
            fatal.append(f"reverse-search 실패: {check.issue_title}/{check.source}")
        elif check.status == CheckStatus.DEGRADED:
            if policy.publish_gate.allow_explicit_reverse_search_degraded:
                warnings.append(f"reverse-search degraded: {check.issue_title}/{check.source}")
            else:
                fatal.append(f"reverse-search degraded 불허: {check.issue_title}/{check.source}")
            reporting.append(
                ReportingItem(
                    cause=f"{check.issue_title}/{check.source}: {check.reason}",
                    fallback="공식 수집 결과와 독립 검색 API 결과를 함께 보존",
                    result=check.status.value,
                    next_action="API 설정 또는 출처 접근 복구 후 다음 실행에서 재확인",
                )
            )

    selected_ids = {candidate_id for issue in plan.issues for candidate_id in issue.candidate_ids}
    candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    missing_body: list[str] = []
    for candidate_id in selected_ids:
        candidate = candidate_by_id.get(candidate_id)
        if candidate is None:
            missing_body.append(candidate_id)
            continue
        if candidate.verification_status == VerificationStatus.BODY_VERIFIED:
            continue
        if (
            policy.publish_gate.broadcast_metadata_exception
            and candidate.source in BROADCAST_SOURCES
            and candidate.selectable
        ):
            continue
        missing_body.append(candidate_id)
    if policy.publish_gate.require_core_article_body and missing_body:
        fatal.append(f"core article body 확인 실패 {len(missing_body)}건")
        reporting.append(
            ReportingItem(
                cause="선정 핵심기사의 공식 원문 본문 확인 실패: " + ", ".join(missing_body),
                fallback="해당 기사의 공식 metadata와 실패 상태만 증거로 보존",
                result="failed",
                next_action="robots·HTTP·본문 선택자를 복구하고 원문 본문 확인 뒤 재편집",
            )
        )

    if audit.fatal_error_count > policy.publish_gate.validator_fatal_errors_max:
        fatal.append(f"validator fatal errors={audit.fatal_error_count}")
        for finding in audit.findings:
            if finding.severity != AuditSeverity.FATAL:
                continue
            reporting.append(
                ReportingItem(
                    cause=f"독립 감사 {finding.code}: {finding.explanation}",
                    fallback="오류가 있는 이슈를 자동 발행에서 제외하지 않고 초안 전체를 차단",
                    result="failed",
                    next_action="원근거와 조사기간을 대조해 재편집한 뒤 독립 감사를 다시 실행",
                )
            )

    if policy.publish_gate.require_final_state_complete:
        if final_state.status != CheckStatus.COMPLETE:
            fatal.append("final-state revalidation이 COMPLETE가 아님")
            for check in final_state.checks:
                if check.status == CheckStatus.COMPLETE:
                    continue
                reporting.append(
                    ReportingItem(
                        cause=f"{check.issue_title}: {check.reason}",
                        fallback="초안의 현재상태 서술을 발행하지 않고 확인된 원문만 보존",
                        result=check.status.value,
                        next_action="선정 출처를 공식 경로로 재수집하고 final-state를 다시 판정",
                    )
                )
        changed = [check for check in final_state.checks if check.changed_after_draft]
        if changed:
            fatal.append("초안 작성 뒤 최종상태 변경이 발견되어 재편집이 필요함")
            for check in changed:
                reporting.append(
                    ReportingItem(
                        cause=f"{check.issue_title}: {check.reason}",
                        fallback="새 후속보도 URL을 evidence에 남기고 기존 초안 발행을 중단",
                        result="changed_after_draft",
                        next_action="후속보도를 초안에 반영하고 편집·감사·gate를 처음부터 재실행",
                    )
                )

    unclassified = sum(detail.unclassified_failures for detail in health.sources.values())
    if unclassified > policy.publish_gate.unclassified_failures_max:
        fatal.append(f"미분류 실패={unclassified}")
        for source, detail in health.sources.items():
            if not detail.unclassified_failures:
                continue
            reporting.append(
                ReportingItem(
                    cause=f"{source}: 분류되지 않은 수집 예외 {detail.unclassified_failures}건",
                    fallback="예외를 성공·무보도로 바꾸지 않고 source isolation 상태로 보존",
                    result="failed",
                    next_action="예외 유형을 명시적으로 분류하고 회귀시험을 추가한 뒤 재실행",
                )
            )

    if gap_detection.status == CheckStatus.DEGRADED:
        warnings.append("독립 gap detector가 명시적 degraded 상태임")
        reporting.append(
            ReportingItem(
                cause="Naver API Hub 누락 탐지층이 완전하게 실행되지 않음",
                fallback="공식 RSS·사이트맵·목록의 결정론적 수집 결과를 사용",
                result="degraded",
                next_action="Naver API Hub 자격증명과 호출 상태를 복구",
            )
        )
    elif gap_detection.status == CheckStatus.FAILED:
        fatal.append("독립 gap detector 실패가 분류됐으나 정책상 안전한 결과를 만들지 못함")
        reporting.append(
            ReportingItem(
                cause="독립 gap detector가 FAILED 상태임",
                fallback="공식 발견 경로 결과를 보존하고 검색결과를 원문 검증으로 대체하지 않음",
                result="failed",
                next_action="독립 검색 경로의 인증·할당량·응답 구조를 복구한 뒤 재실행",
            )
        )
    non_census_gaps = [
        hit
        for hit in gap_detection.potential_gaps
        if hit.matched_source not in policy.publish_gate.disability_press_census_required
    ]
    if non_census_gaps:
        warnings.append(f"독립 검색에서 결정론적 수집에 없는 잠재 누락 {len(non_census_gaps)}건")
        for hit in non_census_gaps[:20]:
            reporting.append(
                ReportingItem(
                    cause=f"{hit.matched_source}: 결정론적 수집에 없는 검색 후보 {hit.title}",
                    fallback="검색 결과 URL을 provenance에 보존하되 본문 확인 근거로 사용하지 않음",
                    result="potential_gap",
                    next_action=(
                        "등록된 공식 경로에서 같은 원문을 재수집하고 adapter 누락 여부를 점검"
                    ),
                )
            )

    return PublishGateDecision(
        report_date=report_date,
        checked_at=datetime.now(UTC),
        allowed=not fatal,
        fatal_errors=fatal,
        degraded_warnings=warnings,
        validator_fatal_errors=audit.fatal_error_count,
        unclassified_failures=unclassified,
        reporting_items=reporting,
    )


def write_assurance_outputs(
    root: Path,
    *,
    manifest: EvidenceManifest,
    gate: PublishGateDecision,
) -> None:
    JsonlStorage.atomic_write_json(
        root / "evidence" / f"{manifest.report_date}.json",
        manifest.model_dump(mode="json"),
    )
    JsonlStorage.atomic_write_json(
        root / "health" / "publish_gate" / "latest.json",
        gate.model_dump(mode="json"),
    )
