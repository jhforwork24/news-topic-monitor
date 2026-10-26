from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from news_topic_monitor.adapters.base import SourceAdapter, StructureChangedError
from news_topic_monitor.adapters.chosun import ChosunAdapter
from news_topic_monitor.coverage import (
    LIST_LAG_MARGIN,
    MISSING_LIST_LIMIT,
    STATUS_ERROR,
    STATUS_FAIL,
    STATUS_NOT_JUDGED,
    STATUS_PASS,
    ListedArticle,
    build_report,
    evaluate,
    list_window,
    merge_into_health,
    run_check,
    target_for,
)
from news_topic_monitor.http import HttpRequestError
from news_topic_monitor.models import ArticleDiscovery, DiscoveryPage
from news_topic_monitor.storage import JsonlStorage

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)
START = NOW - timedelta(hours=48)


def _item(number: int, hours_ago: float | None) -> ListedArticle:
    listed_at = None if hours_ago is None else NOW - timedelta(hours=hours_ago)
    return ListedArticle(
        url=f"https://good.test/article/{number}", title=f"합성 기사 {number}", listed_at=listed_at
    )


def _evaluate(items, stored, *, source="chosun", last_success=None):
    return evaluate(
        source,
        items,
        {item.url for item in items if item.url.rsplit("/", 1)[-1] in stored},
        start=START,
        now=NOW,
        last_success=last_success,
    )


def test_targets_are_strict_for_disability_and_labor_press() -> None:
    assert target_for("beminor") == 1.0
    assert target_for("ablenews") == 1.0
    assert target_for("newscham") == 1.0
    assert target_for("chosun") == 0.99
    assert target_for("hani") == 0.99


def test_capture_rate_counts_only_the_judged_period() -> None:
    # 1~4번은 판정 구간(6시간 전~45시간 전). 5번은 최근 3시간 안이라 아직 수집 전일 수 있어 뺀다.
    items = [_item(1, 40), _item(2, 30), _item(3, 20), _item(4, 10), _item(5, 1)]
    result = _evaluate(items, {"1", "2", "3"})
    assert (result.listed, result.captured, result.missing_total) == (4, 3, 1)
    assert result.rate == pytest.approx(0.75)
    assert result.status == STATUS_FAIL
    assert [m["url"] for m in result.missing] == ["https://good.test/article/4"]


def test_complete_capture_passes_the_target() -> None:
    items = [_item(number, 40 - number) for number in range(1, 11)]
    result = _evaluate(items, {str(number) for number in range(1, 11)}, source="beminor")
    assert result.status == STATUS_PASS
    assert result.rate == 1.0


def test_specialist_press_misses_a_single_article() -> None:
    items = [_item(number, 47 - number * 0.4) for number in range(1, 101)]
    stored = {str(number) for number in range(1, 100)}
    # 99/100 = 99%: 일반 매체는 통과하지만 장애·노동 전문지는 한 건도 놓치면 안 된다.
    assert _evaluate(items, stored, source="hani").status == STATUS_PASS
    assert _evaluate(items, stored, source="ablenews").status == STATUS_FAIL


def test_a_list_only_vouches_for_the_period_it_still_reaches() -> None:
    # 목록이 20시간 전까지만 닿는데 점검 창은 48시간이다. 그보다 앞선 구간은 판정하지 않는다.
    items = [_item(1, 20), _item(2, 12), _item(3, 8)]
    result = _evaluate(items, {"1", "2", "3"})
    assert result.judged_from == NOW - timedelta(hours=20)
    assert result.listed == 3


def test_articles_newer_than_the_last_successful_collection_are_not_missing() -> None:
    items = [_item(1, 30), _item(2, 10), _item(3, 5)]
    last_success = NOW - timedelta(hours=8)
    result = _evaluate(items, {"1"}, last_success=last_success)
    # 8시간 전 수집 이후에 올라온 기사(5시간 전)는 아직 수집 회차를 만나지 못했을 수 있어 뺀다.
    # 마지막 수집 직전 1시간(목록 반영 지연)도 뺀다.
    assert result.judged_until == last_success - LIST_LAG_MARGIN
    assert (result.listed, result.missing_total) == (2, 1)
    assert result.missing[0]["url"] == "https://good.test/article/2"
    near_run = _evaluate([_item(1, 30), _item(2, 8.5)], {"1"}, last_success=last_success)
    assert (near_run.listed, near_run.missing_total) == (1, 0)


def test_undated_listing_items_are_judged_by_presence() -> None:
    items = [_item(1, 30), _item(2, None), _item(3, None)]
    result = _evaluate(items, {"1", "2"})
    assert result.undated == 2
    assert (result.listed, result.captured) == (3, 2)


def test_list_without_any_dated_item_cannot_be_judged() -> None:
    result = _evaluate([_item(1, None)], {"1"})
    assert result.status == STATUS_NOT_JUDGED
    assert result.rate is None


def test_missing_list_is_capped_and_newest_first() -> None:
    items = [_item(number, 40 - number * 0.5) for number in range(1, 61)]
    result = _evaluate(items, set())
    assert result.missing_total == 60
    assert len(result.missing) == MISSING_LIST_LIMIT
    assert result.missing[0]["url"] == "https://good.test/article/60"


class _ListAdapter(SourceAdapter):
    source = "good"
    allowed_discovery_hosts = frozenset({"good.test"})
    allowed_article_hosts = frozenset({"good.test"})

    def __init__(self) -> None:
        self.pages: dict[str, DiscoveryPage] = {}

    def extract_body(self, html_text, url):
        del html_text, url
        raise AssertionError("the capture-rate check never fetches article bodies")

    def initial_discovery_urls(self, start, end):
        del start, end
        return list(self.pages)[:1]

    def parse_discovery(self, content, url):
        del content
        if url not in self.pages:
            raise StructureChangedError("synthetic structure change")
        return self.pages[url]

    def excluded_from_coverage(self, url):
        return "/skip/" in url


class _StubHttp:
    class Response:
        content = b"unused"

        def __init__(self, url):
            self.url = url

    def __init__(self, failing=()):
        self.failing = set(failing)
        self.requested = []

    def get(self, url, *, purpose="metadata", headers=None):
        del headers
        self.requested.append((url, purpose))
        if url in self.failing:
            raise HttpRequestError("synthetic failure", status_code=503)
        return self.Response(url)


def _discovery(number: int, hours_ago: float, *, skip: bool = False) -> ArticleDiscovery:
    kind = "skip" if skip else "article"
    return ArticleDiscovery(
        source="good",
        canonical_url=f"https://good.test/{kind}/{number}",
        title=f"합성 기사 {number}",
        published_at=NOW - timedelta(hours=hours_ago),
    )


def test_list_window_follows_child_pages_and_skips_intentional_exclusions() -> None:
    adapter = _ListAdapter()
    adapter.pages = {
        "https://good.test/list": DiscoveryPage(
            articles=[_discovery(1, 30), _discovery(2, 20, skip=True)],
            child_urls=["https://good.test/list?page=2"],
        ),
        "https://good.test/list?page=2": DiscoveryPage(articles=[_discovery(3, 10)]),
    }
    listed, attempted, succeeded, errors = list_window(
        adapter, _StubHttp(), START, NOW, child_limit=5
    )
    assert sorted(item.url for item in listed) == [
        "https://good.test/article/1",
        "https://good.test/article/3",
    ]
    assert (attempted, succeeded, errors) == (2, 2, [])


def test_list_window_reports_failed_paths_without_stopping() -> None:
    adapter = _ListAdapter()
    adapter.pages = {
        "https://good.test/list": DiscoveryPage(
            articles=[_discovery(1, 30)], child_urls=["https://good.test/list?page=2"]
        ),
        "https://good.test/list?page=2": DiscoveryPage(articles=[_discovery(2, 10)]),
    }
    http = _StubHttp(failing={"https://good.test/list?page=2"})
    listed, attempted, succeeded, errors = list_window(adapter, http, START, NOW, child_limit=5)
    assert [item.url for item in listed] == ["https://good.test/article/1"]
    assert (attempted, succeeded) == (2, 1)
    assert len(errors) == 1 and "HttpRequestError" in errors[0]


def test_child_url_limit_is_reported() -> None:
    adapter = _ListAdapter()
    adapter.pages = {
        "https://good.test/list": DiscoveryPage(
            articles=[_discovery(1, 30)],
            child_urls=[f"https://good.test/list?page={n}" for n in range(2, 6)],
        ),
    }
    _, attempted, _, errors = list_window(adapter, _StubHttp(), START, NOW, child_limit=2)
    assert attempted == 3
    assert "child url limit reached" in errors


def test_run_check_compares_with_storage_and_flags_a_dead_source(tmp_path) -> None:
    storage = JsonlStorage(tmp_path)
    adapter = _ListAdapter()
    adapter.pages = {
        "https://good.test/list": DiscoveryPage(
            articles=[_discovery(1, 30), _discovery(2, 20)],
        ),
    }
    storage.update_source_state("good", {"last_success_at": "2026-10-26T11:00:00Z"})
    results = run_check(
        adapters=[adapter],
        http=_StubHttp(),
        storage=storage,
        hours=48,
        child_limit=5,
        now=NOW,
    )
    # 저장분이 비어 있으면 판정 구간의 목록 항목이 모두 누락이다.
    assert results["good"].status == STATUS_FAIL
    assert results["good"].missing_total == 2

    dead = run_check(
        adapters=[adapter],
        http=_StubHttp(failing={"https://good.test/list"}),
        storage=storage,
        hours=48,
        child_limit=5,
        now=NOW,
    )
    assert dead["good"].status == STATUS_ERROR
    assert dead["good"].paths_succeeded == 0


def test_report_keeps_the_latest_report_of_each_scope() -> None:
    first = build_report(
        {"chosun": _evaluate([_item(1, 30)], {"1"})}, scope="chosun", hours=48, now=NOW
    )
    assert first["failing_sources"] == [] and first["errored_sources"] == []
    weekly = build_report(
        {"hani": _evaluate([_item(1, 30)], set(), source="hani")},
        scope="all",
        hours=168,
        now=NOW,
    )
    assert weekly["failing_sources"] == ["hani"]
    merged = merge_into_health({}, first)
    merged = merge_into_health(merged, weekly)
    assert set(merged["scopes"]) == {"chosun", "all"}
    replaced = merge_into_health(merged, first)
    assert replaced["scopes"]["all"] == weekly


def test_chosun_does_not_count_non_target_sections_as_missing() -> None:
    adapter = ChosunAdapter()
    assert adapter.excluded_from_coverage("https://www.chosun.com/sports/baseball/2026/10/26/X/")
    assert adapter.excluded_from_coverage("https://www.chosun.com/entertainments/music/2026/10/1/")
    assert adapter.excluded_from_coverage("https://www.chosun.com/jp/politics-national-jp/2026/")
    assert not adapter.excluded_from_coverage(
        "https://www.chosun.com/economy/industry-company/2026/10/26/X/"
    )
