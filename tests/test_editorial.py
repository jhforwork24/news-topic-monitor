from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from news_topic_monitor.briefing import build_editorial_briefing, render_briefing_markdown
from news_topic_monitor.briefing_validation import BriefingValidationError, validate_briefing
from news_topic_monitor.editorial import (
    EditorialApiError,
    EditorialValidationError,
    OpenAIEditorialClient,
    OpenAIEditorialSettings,
    article_candidate_id,
)
from news_topic_monitor.models import (
    ArticleRecord,
    BodyStatus,
    Classification,
    EditorialCandidate,
    EditorialIssueDecision,
    EditorialPlan,
    EditorialSection,
    VerificationStatus,
)
from news_topic_monitor.storage import JsonlStorage


def _article(
    source: str = "hani",
    *,
    classification: Classification = Classification.IRRELEVANT,
) -> ArticleRecord:
    published = datetime(2026, 8, 15, 1, tzinfo=UTC)
    return ArticleRecord(
        source=source,
        article_id=f"{source}-1",
        canonical_url=f"https://example.com/{source}/1",
        title="활동지원 제도 개편을 요구한 장애인단체 기자회견",
        byline="김기자",
        section="사회",
        published_at=published,
        updated_at=None,
        first_seen_at=published,
        last_seen_at=published,
        summary=(
            "장애인단체가 지역사회에서 살아갈 권리를 보장하도록 "
            "활동지원 제도를 개편하라고 요구했다."
        ),
        monitor_summary="규칙 판정 결과",
        body_status=BodyStatus.FETCHED,
        content_hash="hash",
        classification=classification,
        topic_score=1.0,
        matched_terms=[],
        excluded_terms=[],
        classification_reason="합성 시험 판정",
        verification_status=VerificationStatus.BODY_VERIFIED,
        collection_error=None,
    )


def _candidate(article: ArticleRecord) -> EditorialCandidate:
    return EditorialCandidate(
        candidate_id=article_candidate_id(article),
        source=article.source,
        canonical_url=article.canonical_url,
        title=article.title,
        byline=article.byline,
        section=article.section,
        published_at=article.published_at,
        summary=article.summary,
        evidence_text=(
            "장애인단체는 활동지원 시간이 부족해 지역사회 생활이 제약된다고 설명했고 "
            "정부에 예산과 인정조사 제도의 개편을 요구했다. 정부는 제도 개선 요구를 "
            "검토하겠다고 밝혔으며 구체적인 시행 일정은 제시하지 않았다."
        ),
        body_status=article.body_status,
        verification_status=article.verification_status,
        rule_classification=article.classification,
        rule_score=article.topic_score,
    )


def _response(payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": json.dumps(payload, ensure_ascii=False)}
                    ],
                }
            ],
        },
    )


def test_editorial_operational_defaults_bound_runtime(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_EDITOR_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    for name in (
        "OPENAI_EDITOR_CHUNK_SIZE",
        "OPENAI_EDITOR_MAX_CANDIDATES",
        "OPENAI_EDITOR_FINAL_CANDIDATES",
        "OPENAI_EDITOR_BODY_FETCH_LIMIT_PER_SOURCE",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = OpenAIEditorialSettings.from_env()

    assert settings.chunk_size == 30
    assert settings.max_candidates == 180
    assert settings.final_candidate_limit == 60
    assert settings.body_fetch_limit_per_source == 24


def test_openai_preflight_uses_small_structured_non_stored_request() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return _response({"ok": True})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.openai.com",
    )
    settings = OpenAIEditorialSettings(enabled=True, api_key="test-key", max_retries=0)

    OpenAIEditorialClient(settings, client=client).preflight()

    assert requests[0]["store"] is False
    assert requests[0]["max_output_tokens"] == 64
    assert requests[0]["text"]["format"]["name"] == "news_editorial_preflight"


def test_openai_error_reports_safe_type_and_code_without_provider_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            429,
            json={
                "error": {
                    "type": "insufficient_quota",
                    "code": "insufficient_quota",
                    "message": "sensitive provider detail must not be retained",
                }
            },
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.openai.com",
    )
    settings = OpenAIEditorialSettings(enabled=True, api_key="test-key", max_retries=0)

    with pytest.raises(EditorialApiError) as captured:
        OpenAIEditorialClient(settings, client=client).preflight()

    assert "insufficient_quota" in str(captured.value)
    assert "sensitive provider detail" not in str(captured.value)


def test_editor_and_independent_auditor_use_strict_non_stored_responses() -> None:
    candidate = _candidate(_article())
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return _response(
                {
                    "assessments": [
                        {
                            "candidate_id": candidate.candidate_id,
                            "verdict": "include",
                            "section": "disability",
                            "issue_label": "활동지원 제도 개편",
                            "importance": 90,
                            "reason": "지역사회 생활의 권리와 국가 책임을 다룬다.",
                        }
                    ]
                }
            )
        if len(requests) == 2:
            return _response(
                {
                    "issues": [
                        {
                            "section": "disability",
                            "title": "활동지원 제도 개편 요구",
                            "keyword": "활동지원 제도 개편",
                            "candidate_ids": [candidate.candidate_id],
                            "summary": (
                                "장애인단체가 지역사회 생활을 보장하기 위한 활동지원 예산과 "
                                "인정조사 제도의 개편을 요구했다."
                            ),
                            "tone_analysis": (
                                "당사자의 요구와 정부의 제도 개선 책임을 중심으로 전했다."
                            ),
                        }
                    ],
                    "exclusions": [],
                }
            )
        return _response({"findings": [], "progressive_issue_titles": []})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.openai.com",
    )
    settings = OpenAIEditorialSettings(enabled=True, api_key="test-key", max_retries=0)
    run = OpenAIEditorialClient(settings, client=client).edit([candidate])

    assert len(run.plan.issues) == 1
    assert run.audit.fatal_error_count == 0
    assert len(requests) == 3
    assert all(request["store"] is False for request in requests)
    assert all(request["text"]["format"]["strict"] is True for request in requests)
    assert requests[0]["text"]["format"]["name"] == "news_editorial_assessments"
    assert requests[1]["text"]["format"]["name"] == "news_editorial_plan"
    assert requests[2]["text"]["format"]["name"] == "news_editorial_independent_audit"


def test_editor_rejects_unrecognized_candidate_id() -> None:
    candidate = _candidate(_article())
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        del request
        call_count += 1
        if call_count == 1:
            return _response(
                {
                    "assessments": [
                        {
                            "candidate_id": candidate.candidate_id,
                            "verdict": "include",
                            "section": "disability",
                            "issue_label": "활동지원",
                            "importance": 80,
                            "reason": "권리 의제다.",
                        }
                    ]
                }
            )
        return _response(
            {
                "issues": [
                    {
                        "section": "disability",
                        "title": "확인되지 않은 기사",
                        "keyword": "확인되지 않은 기사",
                        "candidate_ids": ["invented-id"],
                        "summary": "확인되지 않은 기사에 관한 내용을 정리했다.",
                        "tone_analysis": "",
                    }
                ],
                "exclusions": [],
            }
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.openai.com",
    )
    settings = OpenAIEditorialSettings(enabled=True, api_key="test-key", max_retries=0)
    with pytest.raises(EditorialValidationError, match="미확인 candidate_id"):
        OpenAIEditorialClient(settings, client=client).edit([candidate])


def test_editorial_briefing_accepts_verified_gpt_selection_and_preserves_format(tmp_path) -> None:
    article = _article(classification=Classification.IRRELEVANT)
    storage = JsonlStorage(tmp_path)
    storage.upsert(article)
    candidate_id = article_candidate_id(article)
    plan = EditorialPlan(
        issues=[
            EditorialIssueDecision(
                section=EditorialSection.DISABILITY,
                title="활동지원 제도 개편 요구",
                keyword="활동지원 제도 개편",
                candidate_ids=[candidate_id],
                summary=(
                    "장애인단체가 지역사회 생활을 보장하기 위한 활동지원 제도의 개편을 요구했다."
                ),
                tone_analysis="당사자의 권리 요구와 정부의 책임을 중심으로 전했다.",
            )
        ],
        exclusions=[],
    )
    document = build_editorial_briefing(
        storage,
        plan=plan,
        start=datetime(2026, 8, 15, 0, tzinfo=UTC),
        end=datetime(2026, 8, 16, 0, tzinfo=UTC),
        report_date="2026-08-16",
    )

    validate_briefing(document)
    rendered = render_briefing_markdown(document, crpd_url=None)
    assert "김기자" in rendered
    assert "| 언론사 | 기사 | 발행 |" not in rendered
    assert "### 이슈 요약·보도 논조" in rendered
    assert "KST" not in rendered
    assert "활동지원 제도 개편" in document.overview
    assert "활동지원 제도 개편 요구" not in document.overview
    assert "활동지원 제도 개편" in document.telegram_summary
    assert "활동지원 제도 개편 요구" not in document.telegram_summary


def test_editorial_briefing_rejects_tone_analysis_with_leaning_labels(tmp_path) -> None:
    article = _article(classification=Classification.IRRELEVANT)
    storage = JsonlStorage(tmp_path)
    storage.upsert(article)
    candidate_id = article_candidate_id(article)
    plan = EditorialPlan(
        issues=[
            EditorialIssueDecision(
                section=EditorialSection.DISABILITY,
                title="활동지원 제도 개편 요구",
                keyword="활동지원 제도 개편",
                candidate_ids=[candidate_id],
                summary=(
                    "장애인단체가 지역사회 생활을 보장하기 위한 활동지원 제도의 개편을 요구했다."
                ),
                tone_analysis="한겨레(진보)는 당사자의 권리 요구를 강조했다.",
            )
        ],
        exclusions=[],
    )
    document = build_editorial_briefing(
        storage,
        plan=plan,
        start=datetime(2026, 8, 15, 0, tzinfo=UTC),
        end=datetime(2026, 8, 16, 0, tzinfo=UTC),
        report_date="2026-08-16",
    )
    with pytest.raises(BriefingValidationError, match="진영 라벨"):
        validate_briefing(document)


def test_editorial_briefing_rejects_keyword_that_copies_the_title(tmp_path) -> None:
    article = _article(classification=Classification.IRRELEVANT)
    storage = JsonlStorage(tmp_path)
    storage.upsert(article)
    candidate_id = article_candidate_id(article)
    plan = EditorialPlan(
        issues=[
            EditorialIssueDecision(
                section=EditorialSection.DISABILITY,
                title="활동지원 제도 개편 요구",
                keyword="활동지원 제도 개편 요구",
                candidate_ids=[candidate_id],
                summary=(
                    "장애인단체가 지역사회 생활을 보장하기 위한 활동지원 제도의 개편을 요구했다."
                ),
                tone_analysis="당사자의 권리 요구와 정부의 책임을 중심으로 전했다.",
            )
        ],
        exclusions=[],
    )
    document = build_editorial_briefing(
        storage,
        plan=plan,
        start=datetime(2026, 8, 15, 0, tzinfo=UTC),
        end=datetime(2026, 8, 16, 0, tzinfo=UTC),
        report_date="2026-08-16",
    )
    with pytest.raises(BriefingValidationError, match="키워드 요약이 제목을 그대로 인용함"):
        validate_briefing(document)


def test_editorial_briefing_accepts_summary_ending_in_plain_negative_copula(tmp_path) -> None:
    article = _article(classification=Classification.IRRELEVANT)
    storage = JsonlStorage(tmp_path)
    storage.upsert(article)
    candidate_id = article_candidate_id(article)
    plan = EditorialPlan(
        issues=[
            EditorialIssueDecision(
                section=EditorialSection.DISABILITY,
                title="활동지원 제도 개편 요구",
                keyword="활동지원 제도 개편",
                candidate_ids=[candidate_id],
                summary=(
                    "장애인단체는 예산 항목의 합산 추정액이 약 2조원일 뿐, 확정되거나 지급이 "
                    "약속된 금액은 아니다."
                ),
                tone_analysis="당사자의 권리 요구와 정부의 책임을 중심으로 전했다.",
            )
        ],
        exclusions=[],
    )
    document = build_editorial_briefing(
        storage,
        plan=plan,
        start=datetime(2026, 8, 15, 0, tzinfo=UTC),
        end=datetime(2026, 8, 16, 0, tzinfo=UTC),
        report_date="2026-08-16",
    )

    validate_briefing(document)


def test_editorial_briefing_rejects_summary_in_formal_register(tmp_path) -> None:
    article = _article(classification=Classification.IRRELEVANT)
    storage = JsonlStorage(tmp_path)
    storage.upsert(article)
    candidate_id = article_candidate_id(article)
    plan = EditorialPlan(
        issues=[
            EditorialIssueDecision(
                section=EditorialSection.DISABILITY,
                title="활동지원 제도 개편 요구",
                keyword="활동지원 제도 개편",
                candidate_ids=[candidate_id],
                summary="장애인단체가 활동지원 제도의 개편을 요구했습니다.",
                tone_analysis="당사자의 권리 요구와 정부의 책임을 중심으로 전했다.",
            )
        ],
        exclusions=[],
    )
    document = build_editorial_briefing(
        storage,
        plan=plan,
        start=datetime(2026, 8, 15, 0, tzinfo=UTC),
        end=datetime(2026, 8, 16, 0, tzinfo=UTC),
        report_date="2026-08-16",
    )
    with pytest.raises(BriefingValidationError, match="중립적 서술체가 아님"):
        validate_briefing(document)


def test_editorial_briefing_reorders_labor_section_care_then_poverty_then_labor(
    tmp_path,
) -> None:
    labor_article = _article(source="labortoday")
    poverty_article = _article(source="pressian")
    care_article = _article(source="beminor")
    storage = JsonlStorage(tmp_path)
    for article in (labor_article, poverty_article, care_article):
        storage.upsert(article)

    # Submitted in an arbitrary order — 노동, 빈곤, 돌봄 — to prove the section is
    # reordered by build_editorial_briefing rather than by the plan's own order.
    plan = EditorialPlan(
        issues=[
            EditorialIssueDecision(
                section=EditorialSection.LABOR,
                title="사업장 산업재해 은폐 논란",
                keyword="산업재해 은폐",
                candidate_ids=[article_candidate_id(labor_article)],
                summary="중대재해가 산업안전보건법 위반으로 은폐됐다는 의혹이 제기됐다.",
                tone_analysis="노동조합은 진상규명을 요구했다.",
            ),
            EditorialIssueDecision(
                section=EditorialSection.LABOR,
                title="기초생활보장 부양의무자 기준 논란",
                keyword="부양의무자 기준",
                candidate_ids=[article_candidate_id(poverty_article)],
                summary="빈곤층이 부양의무자 기준 탓에 생계급여를 받지 못한다는 지적이 나왔다.",
                tone_analysis="복지사각지대 해소가 필요하다는 지적이 이어졌다.",
            ),
            EditorialIssueDecision(
                section=EditorialSection.LABOR,
                title="요양보호사 처우 개선 요구",
                keyword="요양보호사 처우",
                candidate_ids=[article_candidate_id(care_article)],
                summary="돌봄노동 종사자들이 활동지원사 처우 개선을 요구했다.",
                tone_analysis="공공돌봄 확대가 필요하다는 목소리가 나왔다.",
            ),
        ],
        exclusions=[],
    )

    document = build_editorial_briefing(
        storage,
        plan=plan,
        start=datetime(2026, 8, 15, 0, tzinfo=UTC),
        end=datetime(2026, 8, 16, 0, tzinfo=UTC),
        report_date="2026-08-16",
    )

    labor_section = next(
        section for section in document.sections if section.title == "II. 노동·돌봄·빈곤"
    )
    assert [issue.title for issue in labor_section.issues] == [
        "요양보호사 처우 개선 요구",
        "기초생활보장 부양의무자 기준 논란",
        "사업장 산업재해 은폐 논란",
    ]


def _labor_plan_and_candidates(subsections: list[str | None], titles: list[str] | None = None):
    from news_topic_monitor.models import LaborSubsection

    candidates: list[EditorialCandidate] = []
    issues: list[EditorialIssueDecision] = []
    for index, subsection in enumerate(subsections):
        article = _article(source="labortoday").model_copy(
            update={
                "article_id": f"labortoday-{index}",
                "canonical_url": f"https://example.com/labortoday/{index}",
            }
        )
        candidate = _candidate(article)
        candidates.append(candidate)
        issues.append(
            EditorialIssueDecision(
                section=EditorialSection.LABOR,
                title=(titles[index] if titles else f"이슈 {index}"),
                keyword=f"키워드 {index}",
                candidate_ids=[candidate.candidate_id],
                summary="사업장에서 일어난 사안을 정리한 문장이다.",
                tone_analysis="",
                subsection=LaborSubsection(subsection) if subsection else None,
            )
        )
    return EditorialPlan(issues=issues, exclusions=[]), candidates


def _empty_audit():
    from news_topic_monitor.models import EditorialAudit

    return EditorialAudit(findings=[], progressive_issue_titles=[])


def test_labor_subsection_caps_accept_maximum_and_zero() -> None:
    from news_topic_monitor.editorial import validate_external_editorial

    plan, candidates = _labor_plan_and_candidates(["care"] * 4 + ["poverty"] * 3)
    validate_external_editorial(plan=plan, audit=_empty_audit(), candidates=candidates)

    # 0 issues in II절 is allowed as long as another section carries the briefing.
    disability = _candidate(_article(source="beminor"))
    zero_labor = EditorialPlan(
        issues=[
            EditorialIssueDecision(
                section=EditorialSection.DISABILITY,
                title="활동지원 제도 개편 요구",
                keyword="활동지원 개편",
                candidate_ids=[disability.candidate_id],
                summary="장애인단체가 제도 개편을 요구했다.",
                tone_analysis="",
            )
        ],
        exclusions=[],
    )
    validate_external_editorial(plan=zero_labor, audit=_empty_audit(), candidates=[disability])


@pytest.mark.parametrize(
    ("subsections", "expected"),
    [
        (["care"] * 5, "돌봄(care) 하위 주제의 이슈가 4개를 초과함"),
        (["poverty"] * 4, "빈곤(poverty) 하위 주제의 이슈가 3개를 초과함"),
        (["labor"] * 4, "노동(labor) 하위 주제의 이슈가 3개를 초과함"),
        (["care"] * 4 + ["poverty"] * 3 + ["labor"], "labor 섹션의 이슈가 7개를 초과함"),
    ],
)
def test_labor_subsection_caps_reject_overflow(subsections: list[str], expected: str) -> None:
    from news_topic_monitor.editorial import validate_external_editorial

    plan, candidates = _labor_plan_and_candidates(subsections)

    with pytest.raises(
        EditorialValidationError, match=expected.replace("(", r"\(").replace(")", r"\)")
    ):
        validate_external_editorial(plan=plan, audit=_empty_audit(), candidates=candidates)


def test_labor_subsection_falls_back_to_term_matching_when_omitted() -> None:
    from news_topic_monitor.editorial import validate_external_editorial

    # Five issues whose titles all contain care terms, no explicit subsection.
    plan, candidates = _labor_plan_and_candidates(
        [None] * 5, titles=[f"요양보호사 처우 {index}" for index in range(5)]
    )

    with pytest.raises(EditorialValidationError, match="돌봄"):
        validate_external_editorial(plan=plan, audit=_empty_audit(), candidates=candidates)


def test_subsection_is_rejected_outside_labor_section() -> None:
    from news_topic_monitor.editorial import validate_external_editorial
    from news_topic_monitor.models import LaborSubsection

    candidate = _candidate(_article(source="beminor"))
    plan = EditorialPlan(
        issues=[
            EditorialIssueDecision(
                section=EditorialSection.DISABILITY,
                title="활동지원 제도 개편 요구",
                keyword="활동지원 개편",
                candidate_ids=[candidate.candidate_id],
                summary="장애인단체가 제도 개편을 요구했다.",
                tone_analysis="",
                subsection=LaborSubsection.CARE,
            )
        ],
        exclusions=[],
    )

    with pytest.raises(EditorialValidationError, match="subsection을 지정할 수 없음"):
        validate_external_editorial(plan=plan, audit=_empty_audit(), candidates=[candidate])


def test_explicit_subsection_overrides_term_matching_in_order(tmp_path) -> None:
    from news_topic_monitor.models import LaborSubsection

    first = _article(source="labortoday")
    second = _article(source="beminor")
    storage = JsonlStorage(tmp_path)
    for article in (first, second):
        storage.upsert(article)
    # Titles carry no care/poverty terms, so only the explicit label can move 돌봄 first.
    plan = EditorialPlan(
        issues=[
            EditorialIssueDecision(
                section=EditorialSection.LABOR,
                title="사업장 임금 체불 논란",
                keyword="임금 체불",
                candidate_ids=[article_candidate_id(first)],
                summary="사업장에서 임금이 체불됐다는 주장이 나왔다.",
                tone_analysis="",
                subsection=LaborSubsection.LABOR,
            ),
            EditorialIssueDecision(
                section=EditorialSection.LABOR,
                title="지역 서비스 종사자 처우 논란",
                keyword="종사자 처우",
                candidate_ids=[article_candidate_id(second)],
                summary="서비스 종사자 처우가 낮다는 지적이 나왔다.",
                tone_analysis="",
                subsection=LaborSubsection.CARE,
            ),
        ],
        exclusions=[],
    )

    document = build_editorial_briefing(
        storage,
        plan=plan,
        start=datetime(2026, 8, 15, 0, tzinfo=UTC),
        end=datetime(2026, 8, 16, 0, tzinfo=UTC),
        report_date="2026-08-16",
    )

    labor_section = next(
        section for section in document.sections if section.title == "II. 노동·돌봄·빈곤"
    )
    assert [issue.title for issue in labor_section.issues] == [
        "지역 서비스 종사자 처우 논란",
        "사업장 임금 체불 논란",
    ]


def test_strict_plan_schema_lists_every_property_as_required() -> None:
    from news_topic_monitor.editorial import _strict_json_schema

    schema = _strict_json_schema(EditorialPlan.model_json_schema())

    decision = schema["$defs"]["EditorialIssueDecision"]
    assert set(decision["required"]) == set(decision["properties"])
    assert "subsection" in decision["required"]
    assert "default" not in decision["properties"]["subsection"]
