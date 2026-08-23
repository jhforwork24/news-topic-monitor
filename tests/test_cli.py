from __future__ import annotations

from datetime import UTC, datetime

from news_topic_monitor.cli import _known_relevant_seed_discoveries
from news_topic_monitor.models import ArticleRecord, BodyStatus, Classification, VerificationStatus
from news_topic_monitor.storage import JsonlStorage


def _article(
    key: str,
    *,
    classification: Classification,
    published_at: datetime,
) -> ArticleRecord:
    return ArticleRecord(
        source="hani",
        article_id=key,
        canonical_url=f"https://example.com/{key}",
        title=f"기사 {key}",
        byline="김기자",
        section="사회",
        published_at=published_at,
        updated_at=None,
        first_seen_at=published_at,
        last_seen_at=published_at,
        summary="합성 시험 요약",
        monitor_summary="규칙 판정 결과",
        body_status=BodyStatus.FETCHED,
        content_hash=key,
        classification=classification,
        topic_score=5.0,
        matched_terms=[],
        excluded_terms=[],
        classification_reason="합성 시험 판정",
        verification_status=VerificationStatus.BODY_VERIFIED,
        collection_error=None,
    )


def test_seed_discoveries_include_relevant_and_review_articles_in_window(tmp_path) -> None:
    storage = JsonlStorage(tmp_path)
    start = datetime(2026, 8, 22, 0, tzinfo=UTC)
    end = datetime(2026, 8, 23, 0, tzinfo=UTC)
    storage.upsert(
        _article("in-window-relevant", classification=Classification.RELEVANT, published_at=start)
    )
    storage.upsert(
        _article("in-window-review", classification=Classification.REVIEW, published_at=start)
    )

    seeds = _known_relevant_seed_discoveries(storage, start=start, end=end)

    assert {discovery.canonical_url for discovery in seeds["hani"]} == {
        "https://example.com/in-window-relevant",
        "https://example.com/in-window-review",
    }
    assert all(discovery.refresh_only for discovery in seeds["hani"])


def test_seed_discoveries_exclude_irrelevant_articles(tmp_path) -> None:
    storage = JsonlStorage(tmp_path)
    start = datetime(2026, 8, 22, 0, tzinfo=UTC)
    end = datetime(2026, 8, 23, 0, tzinfo=UTC)
    storage.upsert(
        _article("irrelevant", classification=Classification.IRRELEVANT, published_at=start)
    )

    seeds = _known_relevant_seed_discoveries(storage, start=start, end=end)

    assert seeds == {}


def test_seed_discoveries_exclude_articles_outside_the_window(tmp_path) -> None:
    storage = JsonlStorage(tmp_path)
    start = datetime(2026, 8, 22, 0, tzinfo=UTC)
    end = datetime(2026, 8, 23, 0, tzinfo=UTC)
    storage.upsert(
        _article(
            "before-window",
            classification=Classification.RELEVANT,
            published_at=datetime(2026, 8, 21, 23, tzinfo=UTC),
        )
    )
    storage.upsert(
        _article(
            "after-window",
            classification=Classification.RELEVANT,
            published_at=datetime(2026, 8, 23, 0, tzinfo=UTC),
        )
    )

    seeds = _known_relevant_seed_discoveries(storage, start=start, end=end)

    assert seeds == {}
