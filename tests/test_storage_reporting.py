from __future__ import annotations

from datetime import UTC, datetime

from news_topic_monitor.models import (
    ArticleRecord,
    BodyStatus,
    Classification,
    StoreResult,
    VerificationStatus,
)
from news_topic_monitor.reporting import generate_report
from news_topic_monitor.storage import JsonlStorage


def record(*, classification: Classification = Classification.REVIEW) -> ArticleRecord:
    now = datetime(2026, 8, 15, 1, tzinfo=UTC)
    return ArticleRecord(
        source="hani",
        article_id="1",
        canonical_url="https://www.hani.co.kr/arti/society/123.html",
        title="장애인 이동권 논의",
        section="사회",
        published_at=datetime(2026, 8, 15, 0, 30, tzinfo=UTC),
        updated_at=None,
        first_seen_at=now,
        last_seen_at=now,
        summary="공개 요약",
        monitor_summary="모니터 규칙상 장애인, 이동권 의제가 확인된 기사다.",
        body_status=BodyStatus.BLOCKED_BY_ROBOTS,
        content_hash=None,
        classification=classification,
        topic_score=6.0,
        matched_terms=["장애인", "이동권"],
        excluded_terms=[],
        classification_reason="사람의 검토가 필요함.",
        verification_status=VerificationStatus.ROBOTS_BLOCKED,
        collection_error=None,
    )


def test_storage_is_idempotent_and_writes_review(tmp_path) -> None:
    storage = JsonlStorage(tmp_path)
    item = record()
    assert storage.upsert(item) == StoreResult.NEW
    item.last_seen_at = datetime(2026, 8, 15, 2, tzinfo=UTC)
    assert storage.upsert(item) == StoreResult.DUPLICATE
    stored = list(storage.iter_articles())
    assert len(stored) == 1
    assert stored[0].last_seen_at.hour == 2
    review_files = list((tmp_path / "data" / "review").glob("*.jsonl"))
    assert len(review_files) == 1
    assert "article body" not in review_files[0].read_text(encoding="utf-8")


def test_report_uses_half_open_window_and_contains_no_body(tmp_path) -> None:
    storage = JsonlStorage(tmp_path)
    storage.upsert(record(classification=Classification.RELEVANT))
    storage.write_health(
        {
            "sources": {
                "hani": {"success": True, "errors": []},
                "chosun": {"success": False, "errors": ["robots unavailable"]},
            }
        }
    )
    path = generate_report(
        storage,
        start=datetime(2026, 8, 14, 0, tzinfo=UTC),
        end=datetime(2026, 8, 16, 0, tzinfo=UTC),
        report_date="2026-08-16",
    )
    text = path.read_text(encoding="utf-8")
    assert "장애인 이동권 논의" in text
    assert "모니터 자체 요약" in text
    assert "robots unavailable" in text
    assert "원문 보관: 하지 않음" in text
