from __future__ import annotations

from datetime import UTC, datetime

from news_topic_monitor.adapters.base import SourceAdapter, StructureChangedError
from news_topic_monitor.classifier import RuleClassifier
from news_topic_monitor.models import ArticleDiscovery, DiscoveryPage
from news_topic_monitor.pipeline import Collector
from news_topic_monitor.storage import JsonlStorage


class StubHttp:
    class Response:
        content = b"unused"
        url = "https://good.test/feed"

    def get(self, url: str, *, purpose: str = "metadata") -> Response:
        del purpose
        if "bad.test" in url:
            raise StructureChangedError("synthetic source failure")
        return self.Response()


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
