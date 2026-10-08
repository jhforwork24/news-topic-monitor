from __future__ import annotations

from datetime import UTC, datetime, timedelta

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


def test_storage_batch_preserves_results_and_flushes_once_per_date(tmp_path, monkeypatch) -> None:
    storage = JsonlStorage(tmp_path)
    first = record()
    second = record(classification=Classification.RELEVANT)
    second.article_id = "2"
    second.canonical_url = "https://www.hani.co.kr/arti/society/456.html"
    writes: list[str] = []
    original_write = storage._write_records

    def counted_write(path, records):
        writes.append(str(path.relative_to(tmp_path)))
        original_write(path, records)

    monkeypatch.setattr(storage, "_write_records", counted_write)
    with storage.batch():
        assert storage.upsert(first) == StoreResult.NEW
        assert storage.upsert(second) == StoreResult.NEW
        assert list(storage.iter_articles()) == []

    stored = list(storage.iter_articles())
    assert {item.article_id for item in stored} == {"1", "2"}
    assert writes.count("data/articles/2026-08-15.jsonl") == 1
    assert writes.count("data/review/2026-08-15.jsonl") == 1


def test_api_record_confirmed_absent_can_be_exactly_removed(tmp_path) -> None:
    storage = JsonlStorage(tmp_path)
    item = record()
    item.source = "api_source"
    item.article_id = "gone123XYZ0"
    item.canonical_url = "https://api.example.test/watch?v=gone123XYZ0"
    item.first_seen_at = datetime(2026, 7, 1, tzinfo=UTC)
    item.last_seen_at = datetime(2026, 7, 1, tzinfo=UTC)
    storage.upsert(item)

    assert storage.delete_by_source_article_ids("api_source", ["different01"]) == 0
    assert storage.delete_by_source_article_ids("api_source", ["gone123XYZ0"]) == 1
    assert list(storage.iter_articles()) == []


def test_report_uses_half_open_window_and_contains_no_body(tmp_path) -> None:
    storage = JsonlStorage(tmp_path)
    storage.upsert(record(classification=Classification.RELEVANT))
    storage.write_health(
        {
            "sources": {
                "hani": {
                    "success": True,
                    "discovery_status": "complete",
                    "errors": [],
                },
                "chosun": {
                    "success": False,
                    "discovery_status": "unavailable",
                    "errors": ["robots unavailable"],
                },
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
    assert "완전 확인" in text
    assert "확인 불능" in text
    assert "원문 보관: 하지 않음" in text


def _only(storage: JsonlStorage) -> ArticleRecord:
    records = list(storage.iter_articles())
    assert len(records) == 1
    return records[0]


def _seen_again(item: ArticleRecord, *, hours: float) -> ArticleRecord:
    again = item.model_copy(deep=True)
    again.last_seen_at = item.last_seen_at + timedelta(hours=hours)
    return again


def _stored_bytes(tmp_path) -> bytes:
    return b"".join(path.read_bytes() for path in sorted((tmp_path / "data").rglob("*.jsonl")))


def test_default_storage_refreshes_last_seen_on_every_sighting(tmp_path) -> None:
    storage = JsonlStorage(tmp_path)
    item = record()
    storage.upsert(item)

    assert storage.upsert(_seen_again(item, hours=1)) == StoreResult.DUPLICATE
    assert _only(storage).last_seen_at == item.last_seen_at + timedelta(hours=1)


def test_throttled_storage_leaves_an_unchanged_article_alone_within_the_interval(tmp_path) -> None:
    storage = JsonlStorage(tmp_path, last_seen_refresh_interval=timedelta(hours=24))
    item = record()
    assert storage.upsert(item) == StoreResult.NEW
    before = _stored_bytes(tmp_path)

    assert storage.upsert(_seen_again(item, hours=3)) == StoreResult.DUPLICATE
    assert storage.upsert(_seen_again(item, hours=23)) == StoreResult.DUPLICATE

    assert _stored_bytes(tmp_path) == before  # 파일이 다시 쓰이지 않는다
    assert _only(storage).last_seen_at == item.last_seen_at


def test_throttled_storage_refreshes_once_the_interval_has_passed(tmp_path) -> None:
    storage = JsonlStorage(tmp_path, last_seen_refresh_interval=timedelta(hours=24))
    item = record()
    storage.upsert(item)

    assert storage.upsert(_seen_again(item, hours=24)) == StoreResult.DUPLICATE
    assert _only(storage).last_seen_at == item.last_seen_at + timedelta(hours=24)


def test_throttled_storage_still_writes_real_changes_immediately(tmp_path) -> None:
    storage = JsonlStorage(tmp_path, last_seen_refresh_interval=timedelta(hours=24))
    item = record()
    storage.upsert(item)

    changed = _seen_again(item, hours=1)
    changed.title = "장애인 이동권 논의 (수정)"
    changed.summary = "바뀐 공개 요약"
    assert storage.upsert(changed) == StoreResult.UPDATED
    stored = list(storage.iter_articles())
    assert len(stored) == 1
    assert stored[0].summary == "바뀐 공개 요약"
    assert stored[0].last_seen_at == item.last_seen_at + timedelta(hours=1)

    routed = _seen_again(changed, hours=2)
    routed.discovery_route = ["official:https://www.hani.co.kr/rss/new"]
    assert storage.upsert(routed) == StoreResult.UPDATED
    assert "official:https://www.hani.co.kr/rss/new" in _only(storage).discovery_route


def test_throttled_storage_skips_the_write_inside_a_batch_too(tmp_path) -> None:
    storage = JsonlStorage(tmp_path, last_seen_refresh_interval=timedelta(hours=24))
    item = record()
    storage.upsert(item)
    before = _stored_bytes(tmp_path)

    with storage.batch():
        assert storage.upsert(_seen_again(item, hours=2)) == StoreResult.DUPLICATE

    assert _stored_bytes(tmp_path) == before
