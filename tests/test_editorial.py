from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from news_topic_monitor.briefing import build_editorial_briefing, render_briefing_markdown
from news_topic_monitor.briefing_validation import validate_briefing
from news_topic_monitor.editorial import (
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


def test_two_pass_editor_uses_strict_non_stored_responses() -> None:
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
        return _response(
            {
                "issues": [
                    {
                        "section": "disability",
                        "title": "활동지원 제도 개편 요구",
                        "candidate_ids": [candidate.candidate_id],
                        "summary": (
                            "장애인단체가 지역사회 생활을 보장하기 위한 활동지원 예산과 "
                            "인정조사 제도의 개편을 요구했다."
                        ),
                        "tone_analysis": "당사자의 요구와 정부의 제도 개선 책임을 중심으로 전했다.",
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
    run = OpenAIEditorialClient(settings, client=client).edit([candidate])

    assert len(run.plan.issues) == 1
    assert len(requests) == 2
    assert all(request["store"] is False for request in requests)
    assert all(request["text"]["format"]["strict"] is True for request in requests)
    assert requests[0]["text"]["format"]["name"] == "news_editorial_assessments"
    assert requests[1]["text"]["format"]["name"] == "news_editorial_plan"


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
