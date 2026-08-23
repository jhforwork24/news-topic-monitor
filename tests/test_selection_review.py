from __future__ import annotations

from datetime import UTC, datetime

from news_topic_monitor.classifier import RuleClassifier
from news_topic_monitor.editorial import article_candidate_id
from news_topic_monitor.models import (
    ArticleRecord,
    BodyStatus,
    Classification,
    EditorialCandidate,
    EditorialExclusion,
    EditorialIssueDecision,
    EditorialPlan,
    EditorialSection,
    VerificationStatus,
)
from news_topic_monitor.selection_review import build_selection_review


def _article(
    key: str,
    *,
    topic_score: float,
    classification: Classification,
    title: str | None = None,
    summary: str | None = None,
) -> ArticleRecord:
    published = datetime(2026, 8, 15, 1, tzinfo=UTC)
    return ArticleRecord(
        source="hani",
        article_id=key,
        canonical_url=f"https://example.com/{key}",
        title=title or f"기사 {key}",
        byline="김기자",
        section="사회",
        published_at=published,
        updated_at=None,
        first_seen_at=published,
        last_seen_at=published,
        summary=summary or "합성 시험 기사 본문 요약",
        monitor_summary="규칙 판정 결과",
        body_status=BodyStatus.FETCHED,
        content_hash=key,
        classification=classification,
        topic_score=topic_score,
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
        evidence_text="합성 시험 근거 본문 " * 10,
        body_status=article.body_status,
        verification_status=article.verification_status,
        rule_classification=article.classification,
        rule_score=article.topic_score,
    )


def _empty_plan() -> EditorialPlan:
    return EditorialPlan(issues=[], exclusions=[])


def test_candidate_pool_ranks_by_disability_score_descending(topics_path) -> None:
    articles = [
        _article("low", topic_score=2.0, classification=Classification.REVIEW),
        _article("high", topic_score=9.0, classification=Classification.RELEVANT),
        _article("mid", topic_score=5.0, classification=Classification.REVIEW),
    ]
    review = build_selection_review(
        articles=articles,
        plan=_empty_plan(),
        candidates=[],
        topics_path=topics_path,
        report_date="2026-08-15",
    )
    assert [entry.article.article_id for entry in review.candidate_pool] == ["high", "mid", "low"]
    assert review.report_date == "2026-08-15"


def test_candidate_pool_size_caps_at_requested_limit(topics_path) -> None:
    articles = [
        _article(str(index), topic_score=float(index), classification=Classification.REVIEW)
        for index in range(10)
    ]
    review = build_selection_review(
        articles=articles,
        plan=_empty_plan(),
        candidates=[],
        topics_path=topics_path,
        report_date="2026-08-15",
        candidate_pool_size=3,
    )
    assert len(review.candidate_pool) == 3
    assert [entry.article.article_id for entry in review.candidate_pool] == ["9", "8", "7"]


def test_candidate_pool_flags_the_article_the_editor_actually_selected(topics_path) -> None:
    selected = _article("selected", topic_score=9.0, classification=Classification.RELEVANT)
    other = _article("other", topic_score=8.5, classification=Classification.RELEVANT)
    candidate = _candidate(selected)
    plan = EditorialPlan(
        issues=[
            EditorialIssueDecision(
                section=EditorialSection.DISABILITY,
                title="이슈 제목",
                candidate_ids=[candidate.candidate_id],
                summary="합성 시험 요약",
                tone_analysis="합성 시험 논조",
            )
        ],
        exclusions=[EditorialExclusion(candidate_id="unrelated", reason="합성 시험 제외")],
    )
    review = build_selection_review(
        articles=[selected, other],
        plan=plan,
        candidates=[candidate],
        topics_path=topics_path,
        report_date="2026-08-15",
    )
    by_id = {entry.article.article_id: entry for entry in review.candidate_pool}
    assert by_id["selected"].selected is True
    assert by_id["other"].selected is False


def test_near_miss_only_includes_articles_below_the_relevant_threshold(topics_path) -> None:
    disability_threshold = RuleClassifier(
        topics_path, topic="disability_rights"
    ).topic.thresholds.relevant
    articles = [
        _article(
            "just_below",
            topic_score=disability_threshold - 0.1,
            classification=Classification.REVIEW,
        ),
        _article(
            "far_below",
            topic_score=disability_threshold - 5.0,
            classification=Classification.REVIEW,
        ),
        _article(
            "above", topic_score=disability_threshold + 1.0, classification=Classification.RELEVANT
        ),
        _article(
            "exactly_at", topic_score=disability_threshold, classification=Classification.RELEVANT
        ),
    ]
    review = build_selection_review(
        articles=articles,
        plan=_empty_plan(),
        candidates=[],
        topics_path=topics_path,
        report_date="2026-08-15",
    )
    disability_near_miss = next(
        topic for topic in review.near_miss if topic.topic_label.startswith("I절")
    )
    assert disability_near_miss.relevant_threshold == disability_threshold
    assert [entry.article.article_id for entry in disability_near_miss.articles] == [
        "just_below",
        "far_below",
    ]


def test_near_miss_count_caps_list_length(topics_path) -> None:
    disability_threshold = RuleClassifier(
        topics_path, topic="disability_rights"
    ).topic.thresholds.relevant
    articles = [
        _article(
            str(index),
            topic_score=disability_threshold - index,
            classification=Classification.REVIEW,
        )
        for index in range(1, 8)
    ]
    review = build_selection_review(
        articles=articles,
        plan=_empty_plan(),
        candidates=[],
        topics_path=topics_path,
        report_date="2026-08-15",
        near_miss_count=2,
    )
    disability_near_miss = next(
        topic for topic in review.near_miss if topic.topic_label.startswith("I절")
    )
    assert [entry.article.article_id for entry in disability_near_miss.articles] == ["1", "2"]


def test_near_miss_for_labor_topic_recomputes_the_unpersisted_score(topics_path) -> None:
    labor_classifier = RuleClassifier(topics_path, topic="labor_care_poverty")
    title = "임금 논란으로 시끄러운 회사"
    expected = labor_classifier.classify(title=title)
    assert expected.classification != Classification.RELEVANT
    assert 0 < expected.topic_score < labor_classifier.topic.thresholds.relevant

    # topic_score/classification on the ArticleRecord reflect disability_rights,
    # not labor_care_poverty, and must be ignored for this topic's ranking.
    article = _article(
        "labor",
        topic_score=1.0,
        classification=Classification.IRRELEVANT,
        title=title,
        summary=None,
    )
    review = build_selection_review(
        articles=[article],
        plan=_empty_plan(),
        candidates=[],
        topics_path=topics_path,
        report_date="2026-08-15",
    )
    labor_near_miss = next(
        topic for topic in review.near_miss if topic.topic_label.startswith("II절")
    )
    assert len(labor_near_miss.articles) == 1
    assert labor_near_miss.articles[0].topic_score == expected.topic_score
