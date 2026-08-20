from __future__ import annotations

import html
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from .assurance import (
    CheckStatus,
    GapDetectionResult,
    ReverseSearchResult,
    ReverseSourceCheck,
    SearchHit,
)
from .models import EditorialPlan
from .policy import BriefingPolicy, SourceRegistry
from .utils import normalize_text, normalize_url, short_error


class NaverConfigurationError(ValueError):
    pass


class NaverApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class NaverSearchSettings:
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    endpoint: str = "https://naverapihub.apigw.ntruss.com/search/v1/news"
    max_retries: int = 2

    @classmethod
    def from_env(cls) -> NaverSearchSettings:
        client_id = os.getenv("NAVER_API_HUB_CLIENT_ID", "").strip()
        client_secret = os.getenv("NAVER_API_HUB_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            raise NaverConfigurationError(
                "NAVER_API_HUB_CLIENT_ID and NAVER_API_HUB_CLIENT_SECRET are required"
            )
        return cls(client_id=client_id, client_secret=client_secret)


class NaverSearchClient:
    """Independent discovery only; results remain grade C until an original is fetched."""

    def __init__(
        self,
        settings: NaverSearchSettings,
        *,
        client: httpx.Client | None = None,
        sleeper=time.sleep,
    ) -> None:
        self.settings = settings
        self._owns_client = client is None
        self._headers = {
            "X-NCP-APIGW-API-KEY-ID": settings.client_id,
            "X-NCP-APIGW-API-KEY": settings.client_secret,
            "Accept": "application/json",
        }
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
        self.sleeper = sleeper

    def __enter__(self) -> NaverSearchClient:
        return self

    def __exit__(self, *args: object) -> None:
        if self._owns_client:
            self.client.close()

    def search(self, query: str, *, display: int = 100) -> list[SearchHit]:
        response: httpx.Response | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                response = self.client.get(
                    self.settings.endpoint,
                    headers=self._headers,
                    params={
                        "query": query,
                        "display": max(1, min(display, 100)),
                        "start": 1,
                        "sort": "date",
                        "format": "json",
                    },
                )
            except httpx.HTTPError as exc:
                if attempt >= self.settings.max_retries:
                    raise NaverApiError(f"Naver API request failed: {exc}") from exc
                self.sleeper(min(2**attempt, 8))
                continue
            if response.status_code < 400:
                break
            if response.status_code not in {408, 429, 500, 502, 503, 504}:
                raise NaverApiError(f"Naver API returned HTTP {response.status_code}")
            if attempt >= self.settings.max_retries:
                raise NaverApiError(f"Naver API retries exhausted at HTTP {response.status_code}")
            self.sleeper(min(2**attempt, 8))
        if response is None:
            raise NaverApiError("Naver API returned no response")
        try:
            payload = response.json()
        except ValueError as exc:
            raise NaverApiError("Naver API response was not JSON") from exc
        items = payload.get("items")
        if not isinstance(items, list):
            raise NaverApiError("Naver API response did not contain an items array")
        results: list[SearchHit] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            original = _optional_url(item.get("originallink"))
            naver = _optional_url(item.get("link"))
            title = _plain_text(item.get("title"))
            if not title or not (original or naver):
                continue
            results.append(
                SearchHit(
                    query=query,
                    title=title,
                    original_url=original,
                    naver_url=naver,
                    published_at=_published_at(item.get("pubDate")),
                    description=_plain_text(item.get("description")) or None,
                )
            )
        return results


def run_gap_detection(
    *,
    client: NaverSearchClient | None,
    configuration_error: str | None,
    policy: BriefingPolicy,
    registry: SourceRegistry,
    known_canonical_urls: set[str],
    start: datetime,
    end: datetime,
) -> GapDetectionResult:
    if client is None:
        return GapDetectionResult(
            status=CheckStatus.DEGRADED,
            route="naver_api_hub",
            queries_attempted=len(policy.gap_detection.queries),
            queries_completed=0,
            errors=[configuration_error or "Naver API Hub is not configured"],
        )
    hits: list[SearchHit] = []
    errors: list[str] = []
    completed = 0
    for query in policy.gap_detection.queries:
        try:
            results = client.search(query, display=policy.gap_detection.maximum_results_per_query)
            completed += 1
        except NaverApiError as exc:
            errors.append(f"{query}: {short_error(exc)}")
            continue
        for item in results:
            if item.published_at is not None and not start <= item.published_at < end:
                continue
            item.matched_source = registry.source_for_url_host(
                urlsplit(item.original_url or item.naver_url or "").hostname
            )
            if item.original_url and item.matched_source:
                item.in_deterministic_collection = item.original_url in known_canonical_urls
            hits.append(item)
    unique: dict[str, SearchHit] = {}
    for hit in hits:
        key = hit.original_url or hit.naver_url or f"{hit.title}:{hit.published_at}"
        unique[key] = hit
    status = (
        CheckStatus.COMPLETE
        if completed == len(policy.gap_detection.queries)
        else CheckStatus.DEGRADED
    )
    return GapDetectionResult(
        status=status,
        route="naver_api_hub",
        queries_attempted=len(policy.gap_detection.queries),
        queries_completed=completed,
        hits=list(unique.values()),
        potential_gaps=[
            hit
            for hit in unique.values()
            if hit.matched_source is not None
            and hit.published_at is not None
            and hit.in_deterministic_collection is False
        ],
        errors=errors,
    )


def run_reverse_search(
    *,
    client: NaverSearchClient | None,
    configuration_error: str | None,
    plan: EditorialPlan,
    policy: BriefingPolicy,
    registry: SourceRegistry,
    start: datetime,
    end: datetime,
) -> ReverseSearchResult:
    checks: list[ReverseSourceCheck] = []
    for issue in plan.issues:
        for source in policy.publish_gate.designated_reverse_search_required:
            source_policy = registry.sources[source]
            if client is None:
                checks.append(
                    ReverseSourceCheck(
                        issue_title=issue.title,
                        source=source,
                        status=CheckStatus.DEGRADED,
                        reason=configuration_error or "Naver API Hub is not configured",
                    )
                )
                continue
            query = f"{issue.title} {source_policy.label}"
            try:
                results = client.search(query, display=100)
            except NaverApiError as exc:
                checks.append(
                    ReverseSourceCheck(
                        issue_title=issue.title,
                        source=source,
                        status=CheckStatus.DEGRADED,
                        reason=short_error(exc) or "Naver reverse search failed",
                    )
                )
                continue
            domains = {domain.lower().removeprefix("www.") for domain in source_policy.domains}
            match_count = 0
            for item in results:
                if item.published_at is not None and not start <= item.published_at < end:
                    continue
                host = urlsplit(item.original_url or item.naver_url or "").hostname
                normalized = (host or "").lower().removeprefix("www.")
                if normalized in domains:
                    match_count += 1
            checks.append(
                ReverseSourceCheck(
                    issue_title=issue.title,
                    source=source,
                    status=CheckStatus.COMPLETE,
                    match_count=match_count,
                    reason="독립 날짜순 역검색 완료",
                )
            )
    if any(check.status == CheckStatus.FAILED for check in checks):
        status = CheckStatus.FAILED
    elif any(check.status == CheckStatus.DEGRADED for check in checks):
        status = CheckStatus.DEGRADED
    else:
        status = CheckStatus.COMPLETE
    return ReverseSearchResult(status=status, checks=checks)


def _plain_text(value: object) -> str:
    return normalize_text(
        BeautifulSoup(html.unescape(str(value or "")), "html.parser").get_text(" ")
    )


def _optional_url(value: object) -> str | None:
    if not value:
        return None
    try:
        return normalize_url(str(value))
    except ValueError:
        return None


def _published_at(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)
