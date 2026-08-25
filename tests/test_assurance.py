from __future__ import annotations

from datetime import UTC, datetime, timedelta

from news_topic_monitor.assurance import (
    CensusCheck,
    CheckStatus,
    FinalStateIssueCheck,
    FinalStateResult,
    GapDetectionResult,
    ReverseSearchResult,
    ReverseSourceCheck,
    SearchHit,
    evaluate_census,
    evaluate_publish_gate,
)
from news_topic_monitor.final_state import revalidate_final_state
from news_topic_monitor.models import (
    AuditSeverity,
    BodyStatus,
    Classification,
    DiscoveryStatus,
    EditorialAudit,
    EditorialAuditFinding,
    EditorialCandidate,
    EditorialIssueDecision,
    EditorialPlan,
    EditorialSection,
    RunHealth,
    SourceHealth,
    VerificationStatus,
)
from news_topic_monitor.policy import load_briefing_policy, load_source_registry


def _health(now: datetime) -> RunHealth:
    sources: dict[str, SourceHealth] = {}
    for source, discovered, oldest in (
        ("beminor", 100, now - timedelta(days=2)),
        ("ablenews", 80, now - timedelta(hours=12)),
        ("theindigo", 5, now - timedelta(hours=12)),
        ("hani", 20, now - timedelta(days=1)),
    ):
        sources[source] = SourceHealth(
            source=source,
            success=True,
            discovery_status=DiscoveryStatus.COMPLETE,
            started_at=now,
            finished_at=now,
            discovered=discovered,
            oldest_discovered_at=oldest,
            discovery_paths_attempted=1,
            discovery_paths_succeeded=1,
        )
    return RunHealth(
        run_started_at=now,
        run_finished_at=now,
        window_start=now - timedelta(days=2),
        window_end=now,
        all_sources_failed=False,
        sources=sources,
    )


def _candidate(candidate_id: str, title: str, published_at: datetime) -> EditorialCandidate:
    return EditorialCandidate(
        candidate_id=candidate_id,
        source="hani",
        canonical_url=f"https://www.hani.co.kr/arti/{candidate_id}",
        title=title,
        byline="기자",
        section="사회",
        published_at=published_at,
        summary="장애인 노동권 보장과 공공의 책임을 다룬 합성 검증용 요약문입니다. " * 3,
        evidence_text="장애인 노동권 보장과 공공의 책임을 다룬 합성 검증용 본문입니다. " * 5,
        body_status=BodyStatus.FETCHED,
        verification_status=VerificationStatus.BODY_VERIFIED,
        rule_classification=Classification.RELEVANT,
        rule_score=10,
    )


def _plan(candidate_id: str) -> EditorialPlan:
    return EditorialPlan(
        issues=[
            EditorialIssueDecision(
                section=EditorialSection.DISABILITY,
                title="권리중심공공일자리 농성",
                keyword="권리중심공공일자리 농성",
                candidate_ids=[candidate_id],
                summary="장애인 노동권 보장을 요구하는 농성이 이어졌다.",
                tone_analysis="권리 요구와 지방정부의 책임을 함께 다뤘다.",
            )
        ],
        exclusions=[],
    )


def test_disability_press_census_requires_boundary_or_exhaustion(tmp_path) -> None:
    del tmp_path
    root = __import__("pathlib").Path(__file__).parents[1]
    registry = load_source_registry(root / "config" / "source-registry.yaml")
    policy = load_briefing_policy(root / "config" / "briefing-policy.yaml")
    now = datetime(2026, 8, 20, 1, tzinfo=UTC)
    health = _health(now)

    checks = evaluate_census(
        health,
        window_start=now - timedelta(days=1),
        registry=registry,
        policy=policy,
    )
    assert [check.source for check in checks] == ["beminor", "ablenews", "theindigo"]
    assert all(check.status == CheckStatus.COMPLETE for check in checks)

    health.sources["beminor"].oldest_discovered_at = now - timedelta(hours=2)
    degraded = evaluate_census(
        health,
        window_start=now - timedelta(days=1),
        registry=registry,
        policy=policy,
    )
    assert degraded[0].status == CheckStatus.DEGRADED


def test_census_uses_discovery_contract_not_unrelated_body_parser_warning() -> None:
    root = __import__("pathlib").Path(__file__).parents[1]
    registry = load_source_registry(root / "config" / "source-registry.yaml")
    policy = load_briefing_policy(root / "config" / "briefing-policy.yaml")
    now = datetime(2026, 8, 20, 1, tzinfo=UTC)
    health = _health(now)
    health.sources["beminor"].structure_warnings.append("article body selector changed")

    body_warning_only = evaluate_census(
        health,
        window_start=now - timedelta(days=1),
        registry=registry,
        policy=policy,
    )
    assert body_warning_only[0].status == CheckStatus.COMPLETE

    health.sources["beminor"].discovery_warnings.append("child sitemap limit reached")
    discovery_warning = evaluate_census(
        health,
        window_start=now - timedelta(days=1),
        registry=registry,
        policy=policy,
    )
    assert discovery_warning[0].status == CheckStatus.DEGRADED


def test_final_state_change_is_detected_from_newer_verified_original() -> None:
    root = __import__("pathlib").Path(__file__).parents[1]
    policy = load_briefing_policy(root / "config" / "briefing-policy.yaml")
    now = datetime(2026, 8, 20, 1, tzinfo=UTC)
    original = _candidate("old", "권리중심공공일자리 농성 돌입", now - timedelta(hours=3))
    update = _candidate("new", "권리중심공공일자리 농성 철수", now - timedelta(minutes=10))
    plan = _plan(original.candidate_id)
    audit = EditorialAudit(findings=[], progressive_issue_titles=["권리중심공공일자리 농성"])

    result = revalidate_final_state(
        plan=plan,
        audit=audit,
        all_candidates=[original, update],
        health=_health(now),
        policy=policy,
        draft_completed_at=now - timedelta(minutes=30),
        checked_at=now,
    )
    assert result.status == CheckStatus.COMPLETE
    assert result.checks[0].changed_after_draft is True
    assert result.checks[0].evidence_urls == [update.canonical_url]


def test_final_state_ignores_newer_article_that_predates_gpt_draft() -> None:
    root = __import__("pathlib").Path(__file__).parents[1]
    policy = load_briefing_policy(root / "config" / "briefing-policy.yaml")
    now = datetime(2026, 8, 20, 1, tzinfo=UTC)
    original = _candidate("old", "권리중심공공일자리 농성 돌입", now - timedelta(hours=3))
    queued_before_draft = _candidate(
        "queued-before-draft",
        "권리중심공공일자리 농성 철수",
        now - timedelta(minutes=45),
    )
    plan = _plan(original.candidate_id)
    audit = EditorialAudit(findings=[], progressive_issue_titles=[plan.issues[0].title])

    result = revalidate_final_state(
        plan=plan,
        audit=audit,
        all_candidates=[original, queued_before_draft],
        health=_health(now),
        policy=policy,
        draft_completed_at=now - timedelta(minutes=30),
        checked_at=now,
    )

    assert result.status == CheckStatus.COMPLETE
    assert result.checks[0].changed_after_draft is False
    assert result.checks[0].evidence_urls == []


def test_final_state_fails_when_recrawl_predates_gpt_draft() -> None:
    root = __import__("pathlib").Path(__file__).parents[1]
    policy = load_briefing_policy(root / "config" / "briefing-policy.yaml")
    now = datetime(2026, 8, 20, 1, tzinfo=UTC)
    original = _candidate("old", "권리중심공공일자리 농성 돌입", now - timedelta(hours=3))
    plan = _plan(original.candidate_id)
    audit = EditorialAudit(findings=[], progressive_issue_titles=[plan.issues[0].title])

    result = revalidate_final_state(
        plan=plan,
        audit=audit,
        all_candidates=[original],
        health=_health(now - timedelta(hours=1)),
        policy=policy,
        draft_completed_at=now,
        checked_at=now,
    )

    assert result.status == CheckStatus.FAILED
    assert "초안·감사 완료 뒤" in result.checks[0].reason


def test_final_state_ignores_unrelated_article_with_only_incidental_body_overlap() -> None:
    # Regression test: a real editorial-finalize run flagged four unrelated
    # labor issues as "changed after draft" because three completely
    # unrelated long articles (about an industrial park investment, a party
    # reshuffle, and a supercomputing project) each happened to share a
    # couple of ordinary words with the issues' summaries and contained a
    # final_state_terms substring somewhere in their body. None of them
    # actually mentioned the issue's subject (HL만도, 평택공장, 유가족...).
    root = __import__("pathlib").Path(__file__).parents[1]
    policy = load_briefing_policy(root / "config" / "briefing-policy.yaml")
    now = datetime(2026, 8, 20, 1, tzinfo=UTC)
    original = _candidate(
        "old", "HL만도 평택공장 비정규직 노동자 끼임 사망", now - timedelta(hours=3)
    )
    unrelated = EditorialCandidate(
        candidate_id="unrelated",
        source="hani",
        canonical_url="https://www.hani.co.kr/arti/unrelated",
        title="새만금 산업단지 대규모 투자 계획 발표",
        byline="기자",
        section="경제",
        published_at=now - timedelta(minutes=10),
        summary="정부는 이번 사업 관리 방안에 합의했으며 책임 소재를 재개 검토하기로 했다. " * 2,
        evidence_text=(
            "정부는 이번 사업 관리 방안에 합의했으며 책임 소재를 재개 검토하기로 했다. " * 5
        ),
        body_status=BodyStatus.FETCHED,
        verification_status=VerificationStatus.BODY_VERIFIED,
        rule_classification=Classification.RELEVANT,
        rule_score=10,
    )
    plan = EditorialPlan(
        issues=[
            EditorialIssueDecision(
                section=EditorialSection.LABOR,
                title="HL만도 평택공장 비정규직 노동자 끼임 사망, 유가족 원청 책임 촉구",
                keyword="HL만도 하청노동자 끼임 사망",
                candidate_ids=[original.candidate_id],
                summary="유가족과 노동단체는 책임 규명과 관리 강화를 요구했다.",
                tone_analysis="원청의 안전관리 부실 책임을 강조하는 논조를 보인다.",
            )
        ],
        exclusions=[],
    )
    audit = EditorialAudit(findings=[], progressive_issue_titles=[plan.issues[0].title])

    result = revalidate_final_state(
        plan=plan,
        audit=audit,
        all_candidates=[original, unrelated],
        health=_health(now),
        policy=policy,
        draft_completed_at=now - timedelta(minutes=30),
        checked_at=now,
    )

    assert result.status == CheckStatus.COMPLETE
    assert result.checks[0].changed_after_draft is False
    assert result.checks[0].evidence_urls == []


def test_final_state_still_detects_follow_up_that_names_the_same_subject() -> None:
    root = __import__("pathlib").Path(__file__).parents[1]
    policy = load_briefing_policy(root / "config" / "briefing-policy.yaml")
    now = datetime(2026, 8, 20, 1, tzinfo=UTC)
    original = _candidate(
        "old", "HL만도 평택공장 비정규직 노동자 끼임 사망", now - timedelta(hours=3)
    )
    follow_up = EditorialCandidate(
        candidate_id="follow-up",
        source="hani",
        canonical_url="https://www.hani.co.kr/arti/follow-up",
        title="HL만도 평택공장 사고 원청 합의 타결",
        byline="기자",
        section="사회",
        published_at=now - timedelta(minutes=10),
        summary="HL만도와 유가족은 원청 책임을 인정하는 합의에 타결했다고 밝혔다. " * 2,
        evidence_text="HL만도와 유가족은 원청 책임을 인정하는 합의에 타결했다고 밝혔다. " * 5,
        body_status=BodyStatus.FETCHED,
        verification_status=VerificationStatus.BODY_VERIFIED,
        rule_classification=Classification.RELEVANT,
        rule_score=10,
    )
    plan = EditorialPlan(
        issues=[
            EditorialIssueDecision(
                section=EditorialSection.LABOR,
                title="HL만도 평택공장 비정규직 노동자 끼임 사망, 유가족 원청 책임 촉구",
                keyword="HL만도 하청노동자 끼임 사망",
                candidate_ids=[original.candidate_id],
                summary="유가족과 노동단체는 책임 규명과 관리 강화를 요구했다.",
                tone_analysis="원청의 안전관리 부실 책임을 강조하는 논조를 보인다.",
            )
        ],
        exclusions=[],
    )
    audit = EditorialAudit(findings=[], progressive_issue_titles=[plan.issues[0].title])

    result = revalidate_final_state(
        plan=plan,
        audit=audit,
        all_candidates=[original, follow_up],
        health=_health(now),
        policy=policy,
        draft_completed_at=now - timedelta(minutes=30),
        checked_at=now,
    )

    assert result.status == CheckStatus.COMPLETE
    assert result.checks[0].changed_after_draft is True
    assert result.checks[0].evidence_urls == [follow_up.canonical_url]


def test_publish_gate_is_machine_checkable_and_fail_closed() -> None:
    root = __import__("pathlib").Path(__file__).parents[1]
    policy = load_briefing_policy(root / "config" / "briefing-policy.yaml")
    now = datetime(2026, 8, 20, 1, tzinfo=UTC)
    candidate = _candidate("selected", "권리중심공공일자리 농성", now - timedelta(hours=2))
    plan = _plan(candidate.candidate_id)
    census = [
        CensusCheck(
            source=source,
            status=CheckStatus.COMPLETE,
            reason="complete",
            discovered=1,
            oldest_discovered_at=now - timedelta(days=1),
            discovery_paths_attempted=1,
            discovery_paths_succeeded=1,
        )
        for source in policy.publish_gate.disability_press_census_required
    ]
    reverse = ReverseSearchResult(
        status=CheckStatus.COMPLETE,
        checks=[
            ReverseSourceCheck(
                issue_title=plan.issues[0].title,
                source=source,
                status=CheckStatus.COMPLETE,
                reason="searched",
            )
            for source in policy.publish_gate.designated_reverse_search_required
        ],
    )
    final_state = FinalStateResult(
        status=CheckStatus.COMPLETE,
        checked_at=now,
        checks=[
            FinalStateIssueCheck(
                issue_title=plan.issues[0].title,
                status=CheckStatus.COMPLETE,
                progressive=True,
                reason="rechecked",
            )
        ],
    )
    audit = EditorialAudit(findings=[], progressive_issue_titles=[plan.issues[0].title])
    gap = GapDetectionResult(
        status=CheckStatus.DEGRADED,
        route="naver_api_hub",
        queries_attempted=5,
        queries_completed=0,
        errors=["not configured"],
    )
    decision = evaluate_publish_gate(
        report_date="2026-08-20",
        policy=policy,
        census=census,
        gap_detection=gap,
        reverse_search=reverse,
        final_state=final_state,
        audit=audit,
        plan=plan,
        candidates=[candidate],
        health=_health(now),
    )
    assert decision.allowed is True
    assert decision.unclassified_failures == 0
    assert decision.degraded_warnings

    fatal_audit = EditorialAudit(
        findings=[
            EditorialAuditFinding(
                severity=AuditSeverity.FATAL,
                issue_title=plan.issues[0].title,
                candidate_ids=[candidate.candidate_id],
                code="outside_window",
                explanation="조사기간 밖 선행보도를 당일 기사로 오인함",
            )
        ],
        progressive_issue_titles=[plan.issues[0].title],
    )
    blocked = evaluate_publish_gate(
        report_date="2026-08-20",
        policy=policy,
        census=census,
        gap_detection=gap,
        reverse_search=reverse,
        final_state=final_state,
        audit=fatal_audit,
        plan=plan,
        candidates=[candidate],
        health=_health(now),
    )
    assert blocked.allowed is False
    assert "validator fatal errors=1" in blocked.fatal_errors
    assert any("outside_window" in item.cause for item in blocked.reporting_items)

    census_gap = gap.model_copy(
        update={
            "potential_gaps": [
                SearchHit(
                    query="장애인 정책",
                    title="공식 목록에서 찾지 못한 장애언론 기사",
                    original_url="https://www.beminor.com/news/articleView.html?idxno=99999",
                    naver_url=None,
                    published_at=now - timedelta(hours=1),
                    description=None,
                    matched_source="beminor",
                    in_deterministic_collection=False,
                )
            ]
        }
    )
    blocked_by_census_gap = evaluate_publish_gate(
        report_date="2026-08-20",
        policy=policy,
        census=census,
        gap_detection=census_gap,
        reverse_search=reverse,
        final_state=final_state,
        audit=audit,
        plan=plan,
        candidates=[candidate],
        health=_health(now),
    )
    assert blocked_by_census_gap.allowed is False
    assert any("census와 모순" in error for error in blocked_by_census_gap.fatal_errors)
