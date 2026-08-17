from __future__ import annotations

from datetime import UTC, datetime

from news_topic_monitor.adapters.base import (
    SourceAdapter,
    SourceConfigurationError,
    StructureChangedError,
)
from news_topic_monitor.classifier import RuleClassifier
from news_topic_monitor.editorial import EditorialEvidenceStore
from news_topic_monitor.http import HttpRequestError
from news_topic_monitor.models import (
    ArticleDiscovery,
    BodyStatus,
    DiscoveryPage,
    DiscoveryStatus,
    VerificationStatus,
)
from news_topic_monitor.pipeline import Collector
from news_topic_monitor.storage import JsonlStorage


class StubHttp:
    def __init__(self) -> None:
        self.requested: list[tuple[str, str]] = []

    class Response:
        content = b"unused"
        text = "<html><body>synthetic public article text</body></html>"
        url = "https://good.test/feed"

    def get(self, url: str, *, purpose: str = "metadata", headers=None) -> Response:
        del headers
        self.requested.append((url, purpose))
        if "bad.test" in url:
            raise StructureChangedError("synthetic source failure")
        response = self.Response()
        response.url = url
        return response


class GoodAdapter(SourceAdapter):
    source = "good"
    allowed_discovery_hosts = frozenset({"good.test"})
    allowed_article_hosts = frozenset({"good.test"})

    def initial_discovery_urls(self, start, end):
        del start, end
        return ["https://good.test/feed"]

    def parse_discovery(self, content, url):
        del content, url
        return DiscoveryPage(
            articles=[
                ArticleDiscovery(
                    source=self.source,
                    canonical_url="https://good.test/article/1",
                    title="일반 경제 기사",
                    published_at=datetime(2026, 8, 15, 1, tzinfo=UTC),
                )
            ]
        )

    def extract_body(self, html_text, url):
        del html_text, url
        raise AssertionError("irrelevant article body must not be requested")


class BadAdapter(GoodAdapter):
    source = "bad"
    allowed_discovery_hosts = frozenset({"bad.test"})
    allowed_article_hosts = frozenset({"bad.test"})

    def initial_discovery_urls(self, start, end):
        del start, end
        return ["https://bad.test/feed"]


class BroadEvidenceAdapter(GoodAdapter):
    source = "broad"

    def extract_body(self, html_text, url):
        del html_text, url
        return "합성 기사 본문으로 규칙과 무관한 후보도 임시 편집 근거에 포함되는지 확인한다."


class ManyBroadEvidenceAdapter(BroadEvidenceAdapter):
    source = "many_broad"

    def parse_discovery(self, content, url):
        del content, url
        return DiscoveryPage(
            articles=[
                ArticleDiscovery(
                    source=self.source,
                    canonical_url=f"https://good.test/article/{number}",
                    title=f"일반 경제 기사 {number}",
                    published_at=datetime(2026, 8, 15, number, tzinfo=UTC),
                )
                for number in (1, 2, 3)
            ]
        )


class MetadataOnlyAdapter(GoodAdapter):
    source = "metadata_only"
    fetch_candidate_bodies = False

    def parse_discovery(self, content, url):
        del content, url
        return DiscoveryPage(
            articles=[
                ArticleDiscovery(
                    source=self.source,
                    canonical_url="https://good.test/article/metadata",
                    title="장애인 이동권 보장 촉구",
                    summary="휠체어 이용자의 이동권 관련 공개 요약",
                    published_at=datetime(2026, 8, 15, 1, tzinfo=UTC),
                )
            ]
        )


class UnconfiguredAdapter(GoodAdapter):
    source = "unconfigured"

    def initial_discovery_urls(self, start, end):
        del start, end
        raise SourceConfigurationError("YOUTUBE_API_KEY is not configured")


class QuotaHttp(StubHttp):
    def get(self, url: str, *, purpose: str = "metadata", headers=None):
        del purpose, headers
        raise HttpRequestError(
            f"GET failed: {url}: HTTP 403",
            status_code=403,
            api_error_reason="quotaExceeded",
        )


def test_source_failure_is_isolated_and_all_failed_is_false(tmp_path, topics_path) -> None:
    health = Collector(
        http=StubHttp(),
        storage=JsonlStorage(tmp_path),
        classifier=RuleClassifier(topics_path),
        adapters=[BadAdapter(), GoodAdapter()],
    ).run(
        datetime(2026, 8, 15, 0, tzinfo=UTC),
        datetime(2026, 8, 15, 2, tzinfo=UTC),
    )
    assert not health.sources["bad"].success
    assert health.sources["good"].success
    assert not health.all_sources_failed
    assert len(list(JsonlStorage(tmp_path).iter_articles())) == 1


def test_all_sources_failed_is_reported(tmp_path, topics_path) -> None:
    health = Collector(
        http=StubHttp(),
        storage=JsonlStorage(tmp_path),
        classifier=RuleClassifier(topics_path),
        adapters=[BadAdapter()],
    ).run(
        datetime(2026, 8, 15, 0, tzinfo=UTC),
        datetime(2026, 8, 15, 2, tzinfo=UTC),
    )
    assert health.all_sources_failed


def test_metadata_only_source_never_requests_article_body(tmp_path, topics_path) -> None:
    http = StubHttp()
    storage = JsonlStorage(tmp_path)
    health = Collector(
        http=http,
        storage=storage,
        classifier=RuleClassifier(topics_path),
        adapters=[MetadataOnlyAdapter()],
    ).run(
        datetime(2026, 8, 15, 0, tzinfo=UTC),
        datetime(2026, 8, 15, 2, tzinfo=UTC),
    )
    assert health.sources["metadata_only"].discovery_status == DiscoveryStatus.COMPLETE
    assert http.requested == [("https://good.test/feed", "discovery")]
    record = next(storage.iter_articles())
    assert record.body_status == BodyStatus.NOT_REQUESTED


def test_missing_source_configuration_has_distinct_health_status(tmp_path, topics_path) -> None:
    health = Collector(
        http=StubHttp(),
        storage=JsonlStorage(tmp_path),
        classifier=RuleClassifier(topics_path),
        adapters=[UnconfiguredAdapter()],
    ).run(
        datetime(2026, 8, 15, 0, tzinfo=UTC),
        datetime(2026, 8, 15, 2, tzinfo=UTC),
    )
    assert health.sources["unconfigured"].discovery_status == (
        DiscoveryStatus.CONFIGURATION_MISSING
    )


def test_api_quota_failure_has_distinct_health_status(tmp_path, topics_path) -> None:
    health = Collector(
        http=QuotaHttp(),
        storage=JsonlStorage(tmp_path),
        classifier=RuleClassifier(topics_path),
        adapters=[GoodAdapter()],
    ).run(
        datetime(2026, 8, 15, 0, tzinfo=UTC),
        datetime(2026, 8, 15, 2, tzinfo=UTC),
    )
    assert health.sources["good"].discovery_status == DiscoveryStatus.QUOTA_EXCEEDED


def test_editorial_collection_temporarily_captures_broad_body_only(tmp_path, topics_path) -> None:
    storage = JsonlStorage(tmp_path)
    evidence_path = tmp_path / "temporary" / "candidates.sqlite3"
    with EditorialEvidenceStore(evidence_path) as evidence_store:
        Collector(
            http=StubHttp(),
            storage=storage,
            classifier=RuleClassifier(topics_path),
            adapters=[BroadEvidenceAdapter()],
            evidence_store=evidence_store,
            capture_all_bodies=True,
        ).run(
            datetime(2026, 8, 15, 0, tzinfo=UTC),
            datetime(2026, 8, 15, 2, tzinfo=UTC),
        )
        candidates = evidence_store.candidates(
            start=datetime(2026, 8, 15, 0, tzinfo=UTC),
            end=datetime(2026, 8, 15, 2, tzinfo=UTC),
        )

    assert len(candidates) == 1
    assert "합성 기사 본문" in candidates[0].evidence_text
    record = next(storage.iter_articles())
    assert record.verification_status == VerificationStatus.BODY_VERIFIED
    persisted = next((tmp_path / "data" / "articles").glob("*.jsonl")).read_text()
    assert "합성 기사 본문" not in persisted


def test_editorial_collection_caps_broad_body_requests_per_source(tmp_path, topics_path) -> None:
    http = StubHttp()
    storage = JsonlStorage(tmp_path)
    evidence_path = tmp_path / "temporary" / "candidates.sqlite3"
    with EditorialEvidenceStore(evidence_path) as evidence_store:
        Collector(
            http=http,
            storage=storage,
            classifier=RuleClassifier(topics_path),
            adapters=[ManyBroadEvidenceAdapter()],
            evidence_store=evidence_store,
            capture_all_bodies=True,
            capture_body_start=datetime(2026, 8, 15, 0, tzinfo=UTC),
            capture_body_limit_per_source=2,
        ).run(
            datetime(2026, 8, 15, 0, tzinfo=UTC),
            datetime(2026, 8, 15, 4, tzinfo=UTC),
        )
        candidates = evidence_store.candidates(
            start=datetime(2026, 8, 15, 0, tzinfo=UTC),
            end=datetime(2026, 8, 15, 4, tzinfo=UTC),
        )

    body_urls = [url for url, purpose in http.requested if purpose == "article body"]
    assert body_urls == ["https://good.test/article/2", "https://good.test/article/3"]
    assert len(candidates) == 3
    assert (
        sum(
            candidate.verification_status == VerificationStatus.BODY_VERIFIED
            for candidate in candidates
        )
        == 2
    )
