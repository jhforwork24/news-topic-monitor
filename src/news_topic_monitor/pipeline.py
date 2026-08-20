from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from pydantic import ValidationError

from .adapters.base import (
    SourceAdapter,
    SourceConfigurationError,
    StructureChangedError,
    metadata_from_html,
)
from .classifier import RuleClassifier
from .http import HttpRequestError, RobotsDeniedError, RobotsUnavailableError, SafeHttpClient
from .models import (
    ArticleDiscovery,
    ArticleRecord,
    BodyStatus,
    Classification,
    ClassificationResult,
    DiscoveryStatus,
    PrimarySourceValidation,
    RunHealth,
    SourceHealth,
    StoreResult,
    VerificationStatus,
)
from .storage import JsonlStorage
from .utils import (
    content_hash,
    in_window,
    normalize_url,
    parse_datetime,
    short_error,
    short_text,
    stable_article_key,
    utc_iso,
)

if TYPE_CHECKING:
    from .editorial import EditorialEvidenceStore

LOGGER = logging.getLogger(__name__)


class DiscoveryPathsFailed(RuntimeError):
    """Every registered discovery path failed with an already classified cause."""


class Collector:
    def __init__(
        self,
        *,
        http: SafeHttpClient,
        storage: JsonlStorage,
        classifier: RuleClassifier,
        adapters: list[SourceAdapter],
        max_discovery_children: int = 20,
        evidence_store: EditorialEvidenceStore | None = None,
        capture_all_bodies: bool = False,
        capture_body_start: datetime | None = None,
        capture_body_limit_per_source: int | None = None,
    ) -> None:
        self.http = http
        self.storage = storage
        self.classifier = classifier
        self.adapters = adapters
        self.max_discovery_children = max_discovery_children
        self.evidence_store = evidence_store
        self.capture_all_bodies = capture_all_bodies
        self.capture_body_start = capture_body_start
        self.capture_body_limit_per_source = capture_body_limit_per_source

    def run(self, start: datetime, end: datetime) -> RunHealth:
        started = datetime.now(UTC)
        sources: dict[str, SourceHealth] = {}
        for adapter in self.adapters:
            health = SourceHealth(source=adapter.source, started_at=datetime.now(UTC))
            sources[adapter.source] = health
            try:
                self._run_source(adapter, start, end, health)
            except SourceConfigurationError as exc:
                LOGGER.warning("source %s is not configured: %s", adapter.source, exc)
                health.errors.append(short_error(exc) or "source configuration is missing")
                health.discovery_status = DiscoveryStatus.CONFIGURATION_MISSING
            except DiscoveryPathsFailed as exc:
                LOGGER.warning("source %s discovery failed: %s", adapter.source, exc)
                health.errors.append(short_error(exc) or "all discovery paths failed")
                health.discovery_status = _failure_status(exc)
            except Exception as exc:  # source isolation boundary
                LOGGER.exception("source %s failed", adapter.source)
                health.errors.append(short_error(exc) or "unknown error")
                health.unclassified_failures += 1
                health.discovery_status = _failure_status(exc)
            finally:
                if health.success:
                    health.discovery_status = (
                        DiscoveryStatus.PARTIAL if health.errors else DiscoveryStatus.COMPLETE
                    )
                health.finished_at = datetime.now(UTC)
                state_values = {
                    "last_run_at": utc_iso(health.finished_at),
                    "success": health.success,
                    "discovery_status": health.discovery_status.value,
                    "discovered": health.discovered,
                    "errors": health.errors,
                }
                if health.success:
                    state_values["last_success_at"] = utc_iso(health.finished_at)
                self.storage.update_source_state(
                    adapter.source,
                    state_values,
                )
        finished = datetime.now(UTC)
        run_health = RunHealth(
            run_started_at=started,
            run_finished_at=finished,
            window_start=start,
            window_end=end,
            all_sources_failed=all(not source.success for source in sources.values()),
            sources=sources,
        )
        self.storage.write_health(run_health.model_dump(mode="json"))
        return run_health

    def _run_source(
        self,
        adapter: SourceAdapter,
        start: datetime,
        end: datetime,
        health: SourceHealth,
    ) -> None:
        queue = list(adapter.initial_discovery_urls(start, end))
        health.discovery_paths_attempted = len(queue)
        visited: set[str] = set()
        discoveries: dict[str, ArticleDiscovery] = {}
        child_count = 0
        successful_pages = 0
        page_errors: list[str] = []
        hani_pagination_stopped = False
        for discovery_url in queue:
            if discovery_url in visited:
                continue
            if hani_pagination_stopped and discovery_url.startswith("https://www.hani.co.kr/arti?"):
                continue
            visited.add(discovery_url)
            try:
                response = self.http.get(
                    discovery_url,
                    purpose="discovery",
                    headers=adapter.discovery_headers(discovery_url),
                )
                page = adapter.parse_discovery(response.content, str(response.url))
                successful_pages += 1
                health.discovery_paths_succeeded += 1
                health.removed += self.storage.delete_by_source_article_ids(
                    adapter.source, page.removed_article_ids
                )
                for article in page.articles:
                    try:
                        article.canonical_url = normalize_url(article.canonical_url)
                    except ValueError as exc:
                        page_errors.append(short_error(exc) or "invalid article URL")
                        continue
                    route = f"official:{discovery_url}"
                    if route not in article.discovery_route:
                        article.discovery_route.append(route)
                    if not adapter.validate_article_url(article.canonical_url):
                        article_host = urlsplit(article.canonical_url).hostname
                        warning = f"rejected unapproved article host: {article_host}"
                        health.structure_warnings.append(warning)
                        health.discovery_warnings.append(warning)
                        continue
                    key = stable_article_key(
                        article.source,
                        article.canonical_url,
                        article.article_id,
                        article.title,
                        article.published_at,
                    )
                    discoveries[key] = _prefer_richer(discoveries.get(key), article)
                for child_url in page.child_urls:
                    if child_count >= self.max_discovery_children:
                        warning = "child sitemap limit reached"
                        health.structure_warnings.append(warning)
                        health.discovery_warnings.append(warning)
                        break
                    if not adapter.validate_child_url(child_url):
                        child_host = urlsplit(child_url).hostname
                        warning = f"rejected unapproved child sitemap host: {child_host}"
                        health.structure_warnings.append(warning)
                        health.discovery_warnings.append(warning)
                        continue
                    queue.append(child_url)
                    child_count += 1
                    health.discovery_paths_attempted += 1
                if adapter.source == "hani" and discovery_url.startswith(
                    "https://www.hani.co.kr/arti?"
                ):
                    dated = [
                        article for article in page.articles if article.published_at is not None
                    ]
                    if dated and min(article.published_at for article in dated) < start:
                        hani_pagination_stopped = True
            except (
                RobotsDeniedError,
                RobotsUnavailableError,
                HttpRequestError,
                StructureChangedError,
            ) as exc:
                page_errors.append(f"{discovery_url}: {short_error(exc)}")
        if successful_pages == 0:
            raise DiscoveryPathsFailed("all discovery paths failed: " + "; ".join(page_errors))
        health.errors.extend(page_errors)
        health.discovered = len(discoveries)
        discovered_dates = [
            item.published_at for item in discoveries.values() if item.published_at is not None
        ]
        if discovered_dates:
            health.oldest_discovered_at = min(discovered_dates)
            health.newest_discovered_at = max(discovered_dates)
        capture_body_keys = self._capture_body_keys(discoveries, start=start, end=end)
        for key, discovery in discoveries.items():
            if not discovery.refresh_only and not in_window(discovery.published_at, start, end):
                continue
            if discovery.refresh_only:
                health.refreshed += 1
            else:
                health.in_window += 1
            self._process_article(
                adapter,
                discovery,
                health,
                capture_body=key in capture_body_keys,
            )
        health.success = True

    def _capture_body_keys(
        self,
        discoveries: dict[str, ArticleDiscovery],
        *,
        start: datetime,
        end: datetime,
    ) -> set[str]:
        if not self.capture_all_bodies:
            return set()
        if self.capture_body_limit_per_source is None and self.capture_body_start is None:
            return set(discoveries)

        body_start = self.capture_body_start or start
        eligible = [
            (key, discovery)
            for key, discovery in discoveries.items()
            if not discovery.refresh_only
            and discovery.published_at is not None
            and body_start <= discovery.published_at < end
        ]
        eligible.sort(key=lambda item: item[1].published_at, reverse=True)
        if self.capture_body_limit_per_source is not None:
            eligible = eligible[: self.capture_body_limit_per_source]
        return {key for key, _ in eligible}

    def _process_article(
        self,
        adapter: SourceAdapter,
        discovery: ArticleDiscovery,
        health: SourceHealth,
        *,
        capture_body: bool = False,
    ) -> None:
        now = datetime.now(UTC)
        first = self.classifier.classify(
            title=discovery.title,
            summary=discovery.summary,
            section=discovery.section,
        )
        result = first
        body_status = (
            BodyStatus.SKIPPED_IRRELEVANT
            if adapter.fetch_candidate_bodies
            else BodyStatus.NOT_REQUESTED
        )
        verification = VerificationStatus.METADATA_ONLY
        body_digest: str | None = None
        body_text: str | None = None
        error: str | None = None

        if (first.candidate or capture_body) and adapter.fetch_candidate_bodies:
            try:
                response = self.http.get(discovery.canonical_url, purpose="article body")
                html_text = response.text
                metadata = metadata_from_html(html_text, str(response.url))
                body = adapter.extract_body(html_text, str(response.url))
                body_text = body
                body_digest = content_hash(body)
                result = self.classifier.classify(
                    title=metadata.get("title") or discovery.title,
                    summary=metadata.get("summary") or discovery.summary,
                    section=metadata.get("section") or discovery.section,
                    body=body,
                )
                discovery = _merge_html_metadata(discovery, metadata)
                body_status = BodyStatus.FETCHED
                verification = VerificationStatus.BODY_VERIFIED
                health.bodies_checked += 1
                # Drop both copyrighted strings before constructing the stored record.
                body = ""
                html_text = ""
            except RobotsDeniedError as exc:
                body_status = BodyStatus.BLOCKED_BY_ROBOTS
                verification = VerificationStatus.ROBOTS_BLOCKED
                health.bodies_blocked += 1
                error = short_error(exc)
            except RobotsUnavailableError as exc:
                body_status = BodyStatus.ROBOTS_UNAVAILABLE
                verification = VerificationStatus.ROBOTS_BLOCKED
                health.bodies_blocked += 1
                error = short_error(exc)
            except HttpRequestError as exc:
                body_status = BodyStatus.HTTP_ERROR
                verification = VerificationStatus.EXTRACTION_FAILED
                error = short_error(exc)
            except (StructureChangedError, ValidationError, ValueError) as exc:
                body_status = BodyStatus.PARSE_ERROR
                verification = VerificationStatus.EXTRACTION_FAILED
                health.structure_warnings.append(short_error(exc) or "body parser error")
                error = short_error(exc)

        record = ArticleRecord(
            source=discovery.source,
            article_id=discovery.article_id,
            canonical_url=discovery.canonical_url,
            title=discovery.title,
            byline=discovery.byline,
            section=discovery.section,
            published_at=discovery.published_at,
            updated_at=discovery.updated_at,
            first_seen_at=now,
            last_seen_at=now,
            summary=short_text(discovery.summary),
            monitor_summary=_monitor_summary(result),
            body_status=body_status,
            content_hash=body_digest,
            classification=result.classification,
            topic_score=result.topic_score,
            matched_terms=result.matched_terms,
            excluded_terms=result.excluded_terms,
            classification_reason=result.classification_reason,
            verification_status=verification,
            discovery_route=discovery.discovery_route,
            primary_source_validation=_primary_source_validation(
                " ".join(
                    value for value in (discovery.title, discovery.summary, body_text) if value
                )
            ),
            collection_error=error,
        )
        if self.evidence_store is not None:
            self.evidence_store.upsert(record, body_text)
        # The evidence store is scoped to the current editorial run. Do not retain
        # article text in the repository-backed ArticleRecord.
        body_text = None
        stored = self.storage.upsert(record)
        if discovery.refresh_only:
            return
        if stored == StoreResult.NEW:
            health.new += 1
        elif stored == StoreResult.UPDATED:
            health.updated += 1
        else:
            health.duplicates += 1
        if result.classification == Classification.RELEVANT:
            health.relevant += 1
        elif result.classification == Classification.REVIEW:
            health.review += 1
        else:
            health.irrelevant += 1


def _prefer_richer(
    existing: ArticleDiscovery | None, incoming: ArticleDiscovery
) -> ArticleDiscovery:
    if existing is None:
        return incoming
    values = incoming.model_dump()
    for field in ("article_id", "byline", "section", "published_at", "updated_at", "summary"):
        if not values.get(field) and getattr(existing, field):
            values[field] = getattr(existing, field)
    if incoming.title.startswith("[제목 미제공]") and not existing.title.startswith(
        "[제목 미제공]"
    ):
        values["title"] = existing.title
    values["discovery_route"] = list(
        dict.fromkeys([*existing.discovery_route, *incoming.discovery_route])
    )
    return ArticleDiscovery.model_validate(values)


def _merge_html_metadata(
    discovery: ArticleDiscovery, metadata: dict[str, str | None]
) -> ArticleDiscovery:
    values = discovery.model_dump()
    for field in ("canonical_url", "title", "byline", "summary", "section"):
        if metadata.get(field):
            values[field] = metadata[field]
    for field in ("published_at", "updated_at"):
        if metadata.get(field):
            values[field] = parse_datetime(metadata[field])
    return ArticleDiscovery.model_validate(values)


def _monitor_summary(result: ClassificationResult) -> str:
    if result.matched_terms:
        issues = ", ".join(result.matched_terms[:4])
        return (
            f"모니터 규칙상 {issues} 의제가 확인되어 {result.classification.value}로 분류된 기사다."
        )
    return (
        f"장애인권 관련 명시적 의제가 확인되지 않아 {result.classification.value}로 분류된 기사다."
    )


PRIMARY_SOURCE_CLAIM_PATTERN = re.compile(
    r"(?:\d[\d,.]*\s*(?:명|건|%|퍼센트|원|억원|조원|시간|곳|개))"
    r"|(?:법률|법|시행령|시행규칙|조례|협약)"
)


def _primary_source_validation(text: str) -> PrimarySourceValidation:
    """Flag claims that need a separate statute/statistics primary-source pass."""

    if PRIMARY_SOURCE_CLAIM_PATTERN.search(text):
        return PrimarySourceValidation.PENDING
    return PrimarySourceValidation.NOT_REQUIRED


def _failure_status(exc: Exception) -> DiscoveryStatus:
    text = str(exc).lower()
    if "quotaexceeded" in text or "quota exceeded" in text:
        return DiscoveryStatus.QUOTA_EXCEEDED
    return DiscoveryStatus.UNAVAILABLE
