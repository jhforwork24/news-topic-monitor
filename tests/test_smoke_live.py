from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from news_topic_monitor.adapters import ALL_ADAPTERS
from news_topic_monitor.adapters.base import StructureChangedError, metadata_from_html
from news_topic_monitor.adapters.hani import HaniAdapter
from news_topic_monitor.http import RobotsDeniedError, RobotsUnavailableError, SafeHttpClient
from news_topic_monitor.settings import ContactRequiredError, Settings

pytestmark = pytest.mark.smoke


@pytest.mark.parametrize("adapter_type", ALL_ADAPTERS, ids=lambda value: value.source)
def test_live_discovery_robots_and_body_parser(adapter_type, tmp_path) -> None:
    if not os.getenv("MONITOR_CONTACT"):
        pytest.skip("MONITOR_CONTACT is required for live smoke tests")
    try:
        settings = Settings.from_env(tmp_path)
    except ContactRequiredError as exc:
        pytest.skip(str(exc))
    adapter = HaniAdapter(max_pages=1) if adapter_type is HaniAdapter else adapter_type()
    end = datetime.now(UTC)
    start = end - timedelta(hours=48)
    discovered = []
    discovery_outcomes = []
    with SafeHttpClient(settings) as http:
        for url in adapter.initial_discovery_urls(start, end)[:2]:
            try:
                response = http.get(url, purpose="smoke discovery")
                page = adapter.parse_discovery(response.content, str(response.url))
                discovered.extend(page.articles)
                discovery_outcomes.append((url, "allowed"))
            except (RobotsDeniedError, RobotsUnavailableError) as exc:
                discovery_outcomes.append((url, type(exc).__name__))
        assert discovery_outcomes
        if adapter.source == "mbc":
            assert all(outcome == "RobotsDeniedError" for _url, outcome in discovery_outcomes)
            assert not discovered
            return
        if adapter.source == "newscham":
            assert all(outcome == "RobotsUnavailableError" for _url, outcome in discovery_outcomes)
            assert not discovered
            return
        assert discovered, f"no article URLs found: {discovery_outcomes}"
        article = discovered[0]
        decision = http.robots_decision(article.canonical_url)
        if not decision.allowed:
            assert decision.status in {"blocked", "unavailable"}
            return
        response = http.get(article.canonical_url, purpose="smoke article body")
        metadata = metadata_from_html(response.text, str(response.url))
        assert metadata.get("title") or article.title
        if adapter.source == "jtbc":
            with pytest.raises(StructureChangedError, match="selector is unavailable"):
                adapter.extract_body(response.text, str(response.url))
            return
        body = adapter.extract_body(response.text, str(response.url))
        assert body.strip()
        body = ""  # explicitly discard live copyrighted text
