from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path

from news_topic_monitor.cli import (
    REVALIDATION_BODY_LIMIT_BASE,
    REVALIDATION_BODY_LIMIT_MAX,
    _finalize_late_recovered_items,
    _known_relevant_seed_discoveries,
    _report_window,
    _retry_timed_out_sources,
    _revalidate_failed_census_sources,
    _revalidation_body_limit,
    _scale_queue_settings,
)
from news_topic_monitor.models import (
    ArticleRecord,
    BodyStatus,
    Classification,
    DiscoveryStatus,
    RunHealth,
    SourceHealth,
    VerificationStatus,
)
from news_topic_monitor.notion_publish import (
    QUEUE_MAX_CANDIDATES_CEILING,
    EditorialQueueSettings,
)
from news_topic_monitor.policy import load_briefing_policy
from news_topic_monitor.storage import JsonlStorage


def test_report_window_defaults_to_the_0500_kst_boundary() -> None:
    args = argparse.Namespace(date="2026-08-26", start=None, end=None)  # 수요일

    date_value, start, end = _report_window(args)

    assert date_value.isoformat() == "2026-08-26"
    assert end == datetime(2026, 8, 25, 20, tzinfo=UTC)  # 2026-08-26 05:00 KST
    assert start == datetime(2026, 8, 24, 20, tzinfo=UTC)  # 2026-08-25 05:00 KST


def test_report_window_on_tuesday_reaches_back_to_the_saturday_boundary() -> None:
    # 발행은 KST 화~토라 월요일 아침 발행이 없다. 화요일 창이 토요일 경계까지 내려가
    # 토·일 보도가 어느 창에도 빠지지 않게 한다.
    args = argparse.Namespace(date="2026-09-22", start=None, end=None)  # 화요일

    _date_value, start, end = _report_window(args)

    assert end == datetime(2026, 9, 21, 20, tzinfo=UTC)  # 2026-09-22 05:00 KST
    assert start == datetime(2026, 9, 18, 20, tzinfo=UTC)  # 2026-09-19 05:00 KST (토)
    assert end - start == timedelta(days=3)


def test_report_window_on_saturday_stays_a_24h_window() -> None:
    args = argparse.Namespace(date="2026-09-19", start=None, end=None)  # 토요일

    _date_value, start, end = _report_window(args)

    assert start == datetime(2026, 9, 17, 20, tzinfo=UTC)  # 2026-09-18 05:00 KST (금)
    assert end - start == timedelta(days=1)


def test_report_window_on_the_tuesday_after_a_skipped_saturday_reaches_back_to_friday() -> None:
    # 2026-09-26(토, 추석)은 사용자 결정으로 건너뛰었다. 그대로면 다음 화요일 창은
    # 평소처럼 토요일 경계(09-26 05:00)까지만 내려가 금 05:00~토 05:00 보도가 어느
    # 창에도 들지 못한다. SKIPPED_PUBLICATION_DATES가 이 날짜를 건너뛰게 해 창이
    # 하루 더 넓어져야 한다.
    args = argparse.Namespace(date="2026-09-29", start=None, end=None)  # 화요일

    _date_value, start, end = _report_window(args)

    assert end == datetime(2026, 9, 28, 20, tzinfo=UTC)  # 2026-09-29 05:00 KST
    assert start == datetime(2026, 9, 24, 20, tzinfo=UTC)  # 2026-09-25 05:00 KST (금)
    assert end - start == timedelta(days=4)


def test_report_window_bridges_the_0700_to_0500_transition_on_2026_09_16() -> None:
    args = argparse.Namespace(date="2026-09-16", start=None, end=None)

    date_value, start, end = _report_window(args)

    assert date_value.isoformat() == "2026-09-16"
    assert end == datetime(2026, 9, 15, 20, tzinfo=UTC)  # 2026-09-16 05:00 KST
    assert start == datetime(2026, 9, 14, 22, tzinfo=UTC)  # 2026-09-15 07:00 KST


def test_report_window_returns_to_the_normal_24h_window_after_the_transition() -> None:
    args = argparse.Namespace(date="2026-09-17", start=None, end=None)

    date_value, start, end = _report_window(args)

    assert date_value.isoformat() == "2026-09-17"
    assert end == datetime(2026, 9, 16, 20, tzinfo=UTC)  # 2026-09-17 05:00 KST
    assert start == datetime(2026, 9, 15, 20, tzinfo=UTC)  # 2026-09-16 05:00 KST


def test_scale_queue_settings_leaves_a_24h_window_untouched() -> None:
    settings = EditorialQueueSettings()
    end = datetime(2026, 9, 21, 20, tzinfo=UTC)

    scaled = _scale_queue_settings(settings, start=end - timedelta(days=1), end=end)

    assert scaled == settings


def test_scale_queue_settings_raises_candidate_caps_on_a_widened_window() -> None:
    settings = EditorialQueueSettings()
    end = datetime(2026, 9, 21, 20, tzinfo=UTC)

    scaled = _scale_queue_settings(settings, start=end - timedelta(days=3), end=end)

    # 180의 3배는 상한 360에서 멈추고, 본문 확인은 24의 3배인 72로 상한(200) 아래에 머문다.
    assert scaled.max_candidates == QUEUE_MAX_CANDIDATES_CEILING
    assert scaled.body_fetch_limit_per_source == 72
    # 섹션별 이슈 상한은 이 경로가 건드리지 않는다.
    assert scaled.chunk_size == settings.chunk_size
    assert scaled.evidence_chars == settings.evidence_chars


def test_report_window_explicit_start_overrides_the_transition() -> None:
    args = argparse.Namespace(date="2026-09-16", start="2026-09-15T12:00:00+09:00", end=None)

    _date_value, start, _end = _report_window(args)

    assert start == datetime(2026, 9, 15, 3, tzinfo=UTC)  # 2026-09-15 12:00 KST


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


def _census_health(now: datetime, *, theindigo_success: bool) -> RunHealth:
    def _source(name: str, *, success: bool) -> SourceHealth:
        return SourceHealth(
            source=name,
            success=success,
            discovery_status=DiscoveryStatus.COMPLETE if success else DiscoveryStatus.UNAVAILABLE,
            started_at=now,
            finished_at=now,
            discovered=100 if success else 0,
            oldest_discovered_at=(now - timedelta(days=2)) if success else None,
            discovery_paths_attempted=1,
            discovery_paths_succeeded=1 if success else 0,
            errors=[] if success else ["all discovery paths failed: robots.txt unavailable"],
        )

    return RunHealth(
        run_started_at=now,
        run_finished_at=now,
        window_start=now - timedelta(hours=48),
        window_end=now,
        all_sources_failed=False,
        sources={
            "beminor": _source("beminor", success=True),
            "ablenews": _source("ablenews", success=True),
            "theindigo": _source("theindigo", success=theindigo_success),
        },
    )


def _briefing_policy():
    root = Path(__file__).parents[1]
    return load_briefing_policy(root / "config" / "briefing-policy.yaml")


def test_revalidate_failed_census_sources_recovers_via_live_retry() -> None:
    now = datetime(2026, 8, 28, 1, tzinfo=UTC)
    health = _census_health(now, theindigo_success=False)
    policy = _briefing_policy()
    calls: list[str] = []

    def fake_collect(source: str) -> RunHealth:
        calls.append(source)
        return _census_health(now, theindigo_success=True)

    result = _revalidate_failed_census_sources(
        None,
        None,
        None,
        health,
        policy=policy,
        sleeper=lambda seconds: None,
        collect=fake_collect,
    )

    assert calls == ["theindigo"]
    assert result.sources["theindigo"].success is True
    assert result.sources["theindigo"].discovered == 100
    # Unaffected sources pass through untouched.
    assert result.sources["beminor"] is health.sources["beminor"]
    assert result.sources["ablenews"] is health.sources["ablenews"]
    # The frozen snapshot object itself is never mutated in place.
    assert health.sources["theindigo"].success is False


def test_revalidate_failed_census_sources_gives_up_after_max_attempts() -> None:
    now = datetime(2026, 8, 28, 1, tzinfo=UTC)
    health = _census_health(now, theindigo_success=False)
    policy = _briefing_policy()
    calls: list[str] = []
    delays: list[float] = []

    def fake_collect(source: str) -> RunHealth:
        calls.append(source)
        return _census_health(now, theindigo_success=False)

    result = _revalidate_failed_census_sources(
        None,
        None,
        None,
        health,
        policy=policy,
        max_attempts=2,
        retry_delay_seconds=5.0,
        sleeper=delays.append,
        collect=fake_collect,
    )

    assert calls == ["theindigo", "theindigo"]
    assert delays == [5.0]  # slept once, between the two attempts, not after the last
    assert result.sources["theindigo"].success is False


def test_revalidate_failed_census_sources_skips_when_nothing_failed() -> None:
    now = datetime(2026, 8, 28, 1, tzinfo=UTC)
    health = _census_health(now, theindigo_success=True)
    policy = _briefing_policy()
    calls: list[str] = []

    result = _revalidate_failed_census_sources(
        None,
        None,
        None,
        health,
        policy=policy,
        collect=lambda source: calls.append(source) or health,
    )

    assert calls == []
    assert result is health


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


def test_revalidation_body_limit_grows_with_the_delay_since_the_window_closed() -> None:
    # A fixed per-source body cap means a late revalidation looks at the same small
    # slice of a source's newest articles, so the follow-up it is meant to find gets
    # pushed out of range — and the result is still reported as "no follow-up found".
    window_end = datetime(2026, 9, 18, 20, tzinfo=UTC)

    at_boundary = _revalidation_body_limit(window_end=window_end, requested_at=window_end)
    prompt = _revalidation_body_limit(
        window_end=window_end, requested_at=window_end + timedelta(minutes=30)
    )
    late = _revalidation_body_limit(
        window_end=window_end, requested_at=window_end + timedelta(hours=6)
    )
    extreme = _revalidation_body_limit(
        window_end=window_end, requested_at=window_end + timedelta(hours=48)
    )

    assert at_boundary == REVALIDATION_BODY_LIMIT_BASE
    # The recrawl window is exactly this gap, so coverage has to grow with it.
    assert prompt > at_boundary
    assert late > prompt
    assert extreme == REVALIDATION_BODY_LIMIT_MAX
    # A revalidation that somehow starts before the boundary never shrinks the cap.
    assert (
        _revalidation_body_limit(
            window_end=window_end, requested_at=window_end - timedelta(hours=1)
        )
        == REVALIDATION_BODY_LIMIT_BASE
    )


ROBOTS_TIMEOUT = (
    "all discovery paths failed: https://www.pressian.com/api/v3/site/rss/news: "
    "robots.txt unavailable for www.pressian.com: GET failed after retries: "
    "https://www.pressian.com/robots.txt: timed out"
)


def _source_health(source: str, now: datetime, *, errors: list[str] | None = None) -> SourceHealth:
    failed = bool(errors)
    return SourceHealth(
        source=source,
        success=not failed,
        discovery_status=DiscoveryStatus.UNAVAILABLE if failed else DiscoveryStatus.COMPLETE,
        started_at=now,
        finished_at=now,
        errors=errors or [],
    )


def _run_health(now: datetime, sources: dict[str, SourceHealth]) -> RunHealth:
    return RunHealth(
        run_started_at=now,
        run_finished_at=now,
        window_start=now - timedelta(hours=24),
        window_end=now,
        all_sources_failed=all(not detail.success for detail in sources.values()),
        sources=sources,
    )


def test_timed_out_source_is_retried_and_reported_when_it_recovers() -> None:
    now = datetime(2026, 10, 1, 0, tzinfo=UTC)
    health = _run_health(
        now,
        {
            "pressian": _source_health("pressian", now, errors=[ROBOTS_TIMEOUT]),
            "hani": _source_health("hani", now),
        },
    )
    calls: list[set[str]] = []
    sleeps: list[float] = []

    def collect(names: set[str]) -> RunHealth:
        calls.append(names)
        later = now + timedelta(minutes=3)
        return _run_health(later, {name: _source_health(name, later) for name in names})

    merged, notes = _retry_timed_out_sources(
        health, collect=collect, rounds=2, delay_seconds=180, sleeper=sleeps.append
    )

    assert calls == [{"pressian"}]  # 성공한 출처는 다시 수집하지 않고, 복구되면 멈춘다
    assert sleeps == [180]
    assert merged.sources["pressian"].success
    assert merged.sources["hani"] is health.sources["hani"]
    assert merged.run_finished_at == now + timedelta(minutes=3)
    assert not merged.all_sources_failed
    assert len(notes) == 1 and "pressian" in notes[0] and "복구" in notes[0]


def test_timed_out_source_still_failing_is_flagged_as_missing_not_as_absent() -> None:
    now = datetime(2026, 10, 1, 0, tzinfo=UTC)
    health = _run_health(
        now,
        {
            "pressian": _source_health("pressian", now, errors=[ROBOTS_TIMEOUT]),
            "hani": _source_health("hani", now),
        },
    )
    rounds_seen: list[int] = []

    def collect(names: set[str]) -> RunHealth:
        rounds_seen.append(len(rounds_seen) + 1)
        return _run_health(
            now, {name: _source_health(name, now, errors=[ROBOTS_TIMEOUT]) for name in names}
        )

    merged, notes = _retry_timed_out_sources(
        health, collect=collect, rounds=2, delay_seconds=0, sleeper=lambda _seconds: None
    )

    assert rounds_seen == [1, 2]
    assert not merged.sources["pressian"].success
    assert len(notes) == 1
    assert "기사 부재가 아님" in notes[0] and "재시도 2회" in notes[0]


def test_only_timeouts_are_retried_other_failures_stay_fail_closed() -> None:
    now = datetime(2026, 10, 1, 0, tzinfo=UTC)
    blocked = "robots.txt unavailable for www.example.com: robots.txt returned HTTP 403"
    health = _run_health(
        now,
        {
            "chosun": _source_health("chosun", now, errors=[blocked]),
            "hani": _source_health("hani", now),
        },
    )

    def collect(names: set[str]) -> RunHealth:
        raise AssertionError("403 robots failures must not be retried")

    merged, notes = _retry_timed_out_sources(
        health, collect=collect, rounds=2, delay_seconds=0, sleeper=lambda _seconds: None
    )

    assert merged is health and notes == []
    # rounds=0 turns the retry off entirely.
    disabled, disabled_notes = _retry_timed_out_sources(
        _run_health(now, {"pressian": _source_health("pressian", now, errors=[ROBOTS_TIMEOUT])}),
        collect=collect,
        rounds=0,
        delay_seconds=0,
    )
    assert disabled_notes == [] and not disabled.sources["pressian"].success


def test_late_recovered_source_articles_are_reported_but_never_block(tmp_path) -> None:
    now = datetime(2026, 10, 1, 0, tzinfo=UTC)
    start = now - timedelta(hours=24)
    initial = _run_health(
        now,
        {
            "pressian": _source_health("pressian", now, errors=[ROBOTS_TIMEOUT]),
            "hani": _source_health("hani", now),
        },
    )
    storage = JsonlStorage(tmp_path)
    missed = _article(
        "missed", classification=Classification.RELEVANT, published_at=now - timedelta(hours=4)
    ).model_copy(update={"source": "pressian"})
    irrelevant = _article(
        "plain", classification=Classification.IRRELEVANT, published_at=now - timedelta(hours=5)
    ).model_copy(update={"source": "pressian"})
    other_source = _article(
        "other", classification=Classification.RELEVANT, published_at=now - timedelta(hours=3)
    )
    for article in (missed, irrelevant, other_source):
        storage.upsert(article)
    recollected: list[str] = []

    def collect(source: str) -> RunHealth:
        recollected.append(source)
        return _run_health(now, {source: _source_health(source, now)})

    settings = type("S", (), {"root": Path(__file__).parents[1]})()
    items = _finalize_late_recovered_items(
        settings,
        storage,
        None,
        initial,
        [],
        start=start,
        end=now,
        collect=collect,
    )

    assert recollected == ["pressian"]  # 시간초과로 빠진 출처만 다시 수집한다
    assert len(items) == 1
    assert items[0].result == "warning"
    assert "pressian" in items[0].cause and "1건" in items[0].cause
    assert "기사 missed" in items[0].cause
