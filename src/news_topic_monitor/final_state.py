from __future__ import annotations

import re
from datetime import UTC, datetime

from .assurance import CheckStatus, FinalStateIssueCheck, FinalStateResult
from .models import EditorialAudit, EditorialCandidate, EditorialPlan, RunHealth, VerificationStatus
from .policy import BriefingPolicy

TOKEN_PATTERN = re.compile(r"[0-9A-Za-z가-힣]{2,}")
GENERIC_TOKENS = frozenset(
    {
        "장애인",
        "노동자",
        "정부",
        "정책",
        "관련",
        "보도",
        "요구",
        "대해",
        "위한",
    }
)


def revalidate_final_state(
    *,
    plan: EditorialPlan,
    audit: EditorialAudit,
    all_candidates: list[EditorialCandidate],
    health: RunHealth,
    policy: BriefingPolicy,
    draft_completed_at: datetime,
    checked_at: datetime | None = None,
) -> FinalStateResult:
    now = checked_at or datetime.now(UTC)
    candidate_by_id = {candidate.candidate_id: candidate for candidate in all_candidates}
    progressive_from_audit = set(audit.progressive_issue_titles)
    checks: list[FinalStateIssueCheck] = []
    for issue in plan.issues:
        selected = [
            candidate_by_id[candidate_id]
            for candidate_id in issue.candidate_ids
            if candidate_id in candidate_by_id
        ]
        issue_text = " ".join(
            [issue.title, issue.summary, *[candidate.title for candidate in selected]]
        )
        progressive = issue.title in progressive_from_audit or any(
            term in issue_text for term in policy.progressive_events.detection_terms
        )
        if not progressive:
            checks.append(
                FinalStateIssueCheck(
                    issue_title=issue.title,
                    status=CheckStatus.COMPLETE,
                    progressive=False,
                    reason="진행형 사건 탐지 조건에 해당하지 않음",
                )
            )
            continue

        if not selected:
            checks.append(
                FinalStateIssueCheck(
                    issue_title=issue.title,
                    status=CheckStatus.FAILED,
                    progressive=True,
                    reason="선정 기사 근거가 재검증 후보 집합에 없음",
                )
            )
            continue

        if (
            health.run_started_at < draft_completed_at
            or health.run_finished_at < draft_completed_at
        ):
            checks.append(
                FinalStateIssueCheck(
                    issue_title=issue.title,
                    status=CheckStatus.FAILED,
                    progressive=True,
                    reason="GPT 초안·감사 완료 뒤에 수행된 공식 재수집 증거가 없음",
                )
            )
            continue

        selected_sources = {candidate.source for candidate in selected}
        unavailable_sources = [
            source
            for source in selected_sources
            if source not in health.sources or not health.sources[source].success
        ]
        if unavailable_sources:
            checks.append(
                FinalStateIssueCheck(
                    issue_title=issue.title,
                    status=CheckStatus.FAILED,
                    progressive=True,
                    reason=(
                        "선정 기사 출처의 발행 직전 공식 재수집 실패: "
                        + ", ".join(sorted(unavailable_sources))
                    ),
                )
            )
            continue

        latest_selected = max(
            (candidate.published_at for candidate in selected if candidate.published_at),
            default=health.window_start,
        )
        issue_tokens = _tokens(issue_text)
        updates: list[EditorialCandidate] = []
        selected_ids = set(issue.candidate_ids)
        for candidate in all_candidates:
            if candidate.candidate_id in selected_ids or candidate.published_at is None:
                continue
            if candidate.published_at <= latest_selected:
                continue
            candidate_text = " ".join(
                [candidate.title, candidate.summary or "", candidate.evidence_text]
            )
            if not any(
                term in candidate_text for term in policy.progressive_events.final_state_terms
            ):
                continue
            if candidate.verification_status != VerificationStatus.BODY_VERIFIED:
                continue
            if _overlap(issue_tokens, _tokens(candidate_text)) < 2:
                continue
            updates.append(candidate)

        if updates:
            checks.append(
                FinalStateIssueCheck(
                    issue_title=issue.title,
                    status=CheckStatus.COMPLETE,
                    progressive=True,
                    changed_after_draft=True,
                    reason="초안·감사 뒤 공식 재수집에서 최종상태 후속보도가 발견됨",
                    evidence_urls=[candidate.canonical_url for candidate in updates[:5]],
                )
            )
        else:
            checks.append(
                FinalStateIssueCheck(
                    issue_title=issue.title,
                    status=CheckStatus.COMPLETE,
                    progressive=True,
                    reason="초안·감사 뒤 공식 출처 재수집에서 더 새로운 최종상태 보도를 찾지 못함",
                )
            )
    status = (
        CheckStatus.COMPLETE
        if all(check.status == CheckStatus.COMPLETE for check in checks)
        else CheckStatus.FAILED
    )
    return FinalStateResult(status=status, checked_at=now, checks=checks)


def _tokens(value: str) -> set[str]:
    return {token for token in TOKEN_PATTERN.findall(value.lower()) if token not in GENERIC_TOKENS}


def _overlap(left: set[str], right: set[str]) -> int:
    return len(left & right)
