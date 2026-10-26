"""Model-free capture-rate check: stored articles against what each source's own lists show.

The shared collector can lose articles without any error: a list that only holds the latest
100 items (조선일보 RSS and sitemaps cover one to two hours on a busy afternoon), scheduled runs
that GitHub skips or delays, or a collection window shorter than the gap between two runs.
This module re-applies every adapter's discovery paths over a check window, without storing
anything, and counts how many of the listed articles the stored data already has. Only official
lists the adapters already use are read, with the crawler's own robots.txt check, request
spacing and contact User-Agent. Article bodies are never fetched or kept.

A list can only vouch for the period it still reaches back to, so each source is judged over the
part of the check window its list covers (`judged_from`). Articles newer than the last
successful collection (or younger than a few hours) are not counted as missing.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from .adapters.base import SourceAdapter, StructureChangedError
from .http import HttpRequestError, RobotsDeniedError, RobotsUnavailableError, SafeHttpClient
from .models import ArticleDiscovery
from .sources import DISABILITY_PRESS_SOURCES, LABOR_ALTERNATIVE_SOURCES
from .storage import JsonlStorage
from .utils import normalize_url, parse_datetime, utc_iso

LOGGER = logging.getLogger(__name__)

SCHEMA_VERSION = 1
# 장애·노동 전문지는 한 건도 놓치지 않는 것이 목표이고, 그 밖의 매체는 99% 이상이다
# (주간 브리핑에서 사용자가 승인한 기준).
TARGET_SPECIALIST = 1.0
TARGET_GENERAL = 0.99
SPECIALIST_SOURCES = DISABILITY_PRESS_SOURCES | LABOR_ALTERNATIVE_SOURCES
# 방금 올라온 기사는 다음 수집 회차를 기다려야 하므로 점검 대상에서 뺀다.
RECENT_GRACE = timedelta(hours=3)
MISSING_LIST_LIMIT = 30

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_ERROR = "error"
STATUS_NOT_JUDGED = "not_judged"


def target_for(source: str) -> float:
    return TARGET_SPECIALIST if source in SPECIALIST_SOURCES else TARGET_GENERAL


@dataclass(frozen=True)
class ListedArticle:
    url: str
    title: str
    listed_at: datetime | None


@dataclass
class SourceCoverage:
    source: str
    target: float
    status: str = STATUS_NOT_JUDGED
    listed: int = 0
    captured: int = 0
    rate: float | None = None
    undated: int = 0
    judged_from: datetime | None = None
    judged_until: datetime | None = None
    paths_attempted: int = 0
    paths_succeeded: int = 0
    errors: list[str] = field(default_factory=list)
    missing_total: int = 0
    missing: list[dict[str, str | None]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "target": self.target,
            "rate": None if self.rate is None else round(self.rate, 4),
            "listed": self.listed,
            "captured": self.captured,
            "undated": self.undated,
            "judged_from": utc_iso(self.judged_from) if self.judged_from else None,
            "judged_until": utc_iso(self.judged_until) if self.judged_until else None,
            "paths_attempted": self.paths_attempted,
            "paths_succeeded": self.paths_succeeded,
            "errors": self.errors,
            "missing_total": self.missing_total,
            "missing": self.missing,
            "notes": self.notes,
        }


def list_window(
    adapter: SourceAdapter,
    http: SafeHttpClient,
    start: datetime,
    end: datetime,
    *,
    child_limit: int,
) -> tuple[list[ListedArticle], int, int, list[str]]:
    """Walk the adapter's discovery paths over the window; nothing is stored."""

    queue = list(adapter.initial_discovery_urls(start, end))
    attempted = len(queue)
    succeeded = 0
    visited: set[str] = set()
    listed: dict[str, ListedArticle] = {}
    errors: list[str] = []
    child_count = 0
    list_prefix = adapter.date_ordered_list_prefix
    pagination_stopped = False
    for url in queue:
        if url in visited:
            continue
        if pagination_stopped and list_prefix is not None and url.startswith(list_prefix):
            continue
        visited.add(url)
        try:
            response = http.get(url, purpose="coverage", headers=adapter.discovery_headers(url))
            page = adapter.parse_discovery(response.content, str(response.url))
        except (
            RobotsDeniedError,
            RobotsUnavailableError,
            HttpRequestError,
            StructureChangedError,
        ) as exc:
            errors.append(f"{urlsplit(url).path[:60]}: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        succeeded += 1
        for article in page.articles:
            item = _listed_article(adapter, article)
            if item is not None:
                listed.setdefault(item.url, item)
        for child_url in page.child_urls:
            if child_count >= child_limit:
                errors.append("child url limit reached")
                break
            if not adapter.validate_child_url(child_url):
                continue
            queue.append(child_url)
            child_count += 1
            attempted += 1
        if list_prefix is not None and url.startswith(list_prefix):
            dated = [a.published_at for a in page.articles if a.published_at is not None]
            if dated and min(dated) < start:
                pagination_stopped = True
    return list(listed.values()), attempted, succeeded, errors


def _listed_article(adapter: SourceAdapter, article: ArticleDiscovery) -> ListedArticle | None:
    try:
        url = normalize_url(article.canonical_url)
    except ValueError:
        return None
    if not adapter.validate_article_url(url) or adapter.excluded_from_coverage(url):
        return None
    return ListedArticle(url=url, title=article.title, listed_at=article.published_at)


def evaluate(
    source: str,
    listed: Iterable[ListedArticle],
    stored_urls: set[str],
    *,
    start: datetime,
    now: datetime,
    last_success: datetime | None,
) -> SourceCoverage:
    """Compare a source's listed articles with the stored URLs.

    The judged period starts at the later of the check window start and the oldest dated
    article the list still shows (older articles may have scrolled off, so their absence
    from the list says nothing) and ends before the last successful collection and the
    recent-grace cutoff.
    """

    coverage = SourceCoverage(source=source, target=target_for(source))
    items = list(listed)
    dated = [item.listed_at for item in items if item.listed_at is not None]
    until = now - RECENT_GRACE
    if last_success is not None:
        until = min(until, last_success)
    coverage.judged_until = until
    if not dated:
        coverage.status = STATUS_NOT_JUDGED
        coverage.undated = len(items)
        coverage.notes.append("발행 시각이 있는 목록 항목이 없어 구간을 판정할 수 없음")
        return coverage
    floor = max(start, min(dated))
    coverage.judged_from = floor
    judged = [item for item in items if item.listed_at is None or (floor <= item.listed_at < until)]
    coverage.undated = sum(1 for item in judged if item.listed_at is None)
    coverage.listed = len(judged)
    missing = [item for item in judged if item.url not in stored_urls]
    coverage.captured = coverage.listed - len(missing)
    coverage.missing_total = len(missing)
    missing.sort(key=lambda item: item.listed_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    coverage.missing = [
        {
            "url": item.url,
            "title": item.title,
            "published_at": utc_iso(item.listed_at) if item.listed_at else None,
        }
        for item in missing[:MISSING_LIST_LIMIT]
    ]
    if coverage.listed == 0:
        coverage.status = STATUS_NOT_JUDGED
        coverage.notes.append("판정 구간에 목록 항목이 없음")
        return coverage
    coverage.rate = coverage.captured / coverage.listed
    coverage.status = STATUS_PASS if coverage.rate >= coverage.target else STATUS_FAIL
    return coverage


def stored_urls_by_source(storage: JsonlStorage, sources: set[str]) -> dict[str, set[str]]:
    urls: dict[str, set[str]] = {source: set() for source in sources}
    for article in storage.iter_articles():
        if article.source not in urls:
            continue
        try:
            urls[article.source].add(normalize_url(article.canonical_url))
        except ValueError:
            continue
    return urls


def run_check(
    *,
    adapters: list[SourceAdapter],
    http: SafeHttpClient,
    storage: JsonlStorage,
    hours: float,
    child_limit: int,
    now: datetime | None = None,
) -> dict[str, SourceCoverage]:
    moment = now or datetime.now(UTC)
    start = moment - timedelta(hours=hours)
    stored = stored_urls_by_source(storage, {adapter.source for adapter in adapters})
    state = storage.load_state().get("sources", {})
    results: dict[str, SourceCoverage] = {}
    for adapter in adapters:
        target = target_for(adapter.source)
        try:
            listed, attempted, succeeded, errors = list_window(
                adapter,
                http,
                start,
                moment,
                child_limit=max(child_limit, adapter.discovery_child_limit or 0),
            )
        except Exception as exc:  # one source must not stop the others
            LOGGER.exception("coverage check failed for %s", adapter.source)
            results[adapter.source] = SourceCoverage(
                source=adapter.source,
                target=target,
                status=STATUS_ERROR,
                errors=[f"{type(exc).__name__}: {str(exc)[:160]}"],
            )
            continue
        last_success = _last_success(state.get(adapter.source, {}))
        coverage = evaluate(
            adapter.source,
            listed,
            stored[adapter.source],
            start=start,
            now=moment,
            last_success=last_success,
        )
        coverage.paths_attempted = attempted
        coverage.paths_succeeded = succeeded
        coverage.errors = errors
        if succeeded == 0:
            coverage.status = STATUS_ERROR
            coverage.notes.append("모든 목록 경로가 실패해 판정하지 않음")
        results[adapter.source] = coverage
    return results


def _last_success(state: dict[str, Any]) -> datetime | None:
    return parse_datetime(state.get("last_success_at"))


def build_report(
    results: dict[str, SourceCoverage], *, scope: str, hours: float, now: datetime
) -> dict[str, Any]:
    failing = sorted(name for name, item in results.items() if item.status == STATUS_FAIL)
    errored = sorted(name for name, item in results.items() if item.status == STATUS_ERROR)
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": scope,
        "generated_at": utc_iso(now),
        "check_hours": hours,
        "targets": {"specialist": TARGET_SPECIALIST, "general": TARGET_GENERAL},
        "failing_sources": failing,
        "errored_sources": errored,
        "sources": {name: item.as_dict() for name, item in sorted(results.items())},
    }


def merge_into_health(existing: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    """Keep the latest report of each scope (daily Chosun and weekly all-source) in one file."""

    merged = dict(existing) if existing.get("schema_version") == SCHEMA_VERSION else {}
    merged["schema_version"] = SCHEMA_VERSION
    scopes = dict(merged.get("scopes", {}))
    scopes[report["scope"]] = report
    merged["scopes"] = scopes
    return merged
