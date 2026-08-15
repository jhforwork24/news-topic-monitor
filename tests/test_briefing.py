from __future__ import annotations

from datetime import UTC, datetime

from news_topic_monitor.briefing import (
    build_briefing,
    is_opinion,
    render_briefing_markdown,
)
from news_topic_monitor.models import (
    ArticleRecord,
    BodyStatus,
    Classification,
    VerificationStatus,
)
from news_topic_monitor.storage import JsonlStorage


def _article(
    source: str,
    title: str,
    *,
    classification: Classification = Classification.RELEVANT,
    section: str = "사회",
    article_id: str = "1",
) -> ArticleRecord:
    now = datetime(2026, 8, 15, 1, tzinfo=UTC)
    return ArticleRecord(
        source=source,
        article_id=f"{source}-{article_id}",
        canonical_url=f"https://example.com/{source}/{article_id}",
        title=title,
        section=section,
        published_at=now,
        updated_at=None,
        first_seen_at=now,
        last_seen_at=now,
        summary="공개 요약",
        monitor_summary="자동 모니터 요약",
        body_status=BodyStatus.FETCHED,
        content_hash="hash",
        classification=classification,
        topic_score=10.0,
        matched_terms=["장애인", "이동권"] if classification != Classification.IRRELEVANT else [],
        excluded_terms=[],
        classification_reason="규칙 판정",
        verification_status=VerificationStatus.BODY_VERIFIED,
        collection_error=None,
    )


def test_four_section_briefing_and_reverse_opinion(tmp_path, topics_path) -> None:
    storage = JsonlStorage(tmp_path)
    rows = [
        _article("hani", "장애인 이동권 보장 촉구", article_id="1"),
        _article(
            "labortoday",
            "돌봄노동자 임금 교섭",
            classification=Classification.IRRELEVANT,
            article_id="2",
        ),
        _article("kbs", "장애인 이동권 현장 리포트", article_id="3"),
        _article("khan", "[칼럼] 장애인 이동권을 시민권으로", section="오피니언", article_id="4"),
    ]
    for row in rows:
        storage.upsert(row)
    storage.write_health(
        {
            "sources": {
                source: {"success": True, "errors": []} for source in {row.source for row in rows}
            }
        }
    )
    document = build_briefing(
        storage,
        topics_path=topics_path,
        start=datetime(2026, 8, 15, 0, tzinfo=UTC),
        end=datetime(2026, 8, 16, 0, tzinfo=UTC),
        report_date="2026-08-16",
    )
    assert [section.title for section in document.sections] == [
        "I. 장애정책·장애인운동",
        "II. 노동·돌봄·빈곤",
        "III. 방송 뉴스 중 장애 주제",
        "IV. 주요 칼럼",
    ]
    assert document.sections[1].issues
    assert document.sections[3].issues
    text = render_briefing_markdown(document, crpd_url="https://notion.example/crpd")
    assert text.index("I. 장애정책") < text.index("II. 노동") < text.index("III. 방송")
    assert "<details>" in text
    assert "https://notion.example/crpd" in text
    assert "본문 원문은 저장하지 않았으며" in text


def test_opinion_detection() -> None:
    assert is_opinion(_article("donga", "[사설] 이동권은 시민권이다", section="오피니언"))
    assert not is_opinion(_article("donga", "이동권 집회 현장 보도"))


def test_markdown_article_link_escapes_brackets(tmp_path, topics_path) -> None:
    storage = JsonlStorage(tmp_path)
    storage.upsert(_article("hani", "[현장] 장애인 이동권 | 보도"))
    document = build_briefing(
        storage,
        topics_path=topics_path,
        start=datetime(2026, 8, 15, 0, tzinfo=UTC),
        end=datetime(2026, 8, 16, 0, tzinfo=UTC),
        report_date="2026-08-16",
    )
    text = render_briefing_markdown(document, crpd_url=None)
    assert r"[\[현장\] 장애인 이동권 \| 보도](https://example.com/hani/1)" in text
