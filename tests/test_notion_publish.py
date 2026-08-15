from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from news_topic_monitor.briefing import BriefingDocument, BriefingSection
from news_topic_monitor.notion_publish import (
    MANAGED_MARKER,
    NotionApiError,
    NotionPublisher,
    NotionPublishSettings,
    notion_blocks,
)


def _document() -> BriefingDocument:
    return BriefingDocument(
        report_date="2026-08-16",
        start=datetime(2026, 8, 15, tzinfo=UTC),
        end=datetime(2026, 8, 16, tzinfo=UTC),
        overview="총평",
        sections=[BriefingSection("I. 장애정책·장애인운동", [])],
        source_failures=["MBC: robots.txt 전면 금지"],
    )


def test_notion_blocks_start_with_overview_then_managed_marker() -> None:
    blocks = notion_blocks(_document(), crpd_url=None)
    assert blocks[0]["paragraph"]["rich_text"][0]["text"]["content"] == "총평"
    assert blocks[1]["callout"]["rich_text"][0]["text"]["content"] == MANAGED_MARKER
    assert any(block["type"] == "heading_1" for block in blocks)


def test_notion_publish_creates_page_and_children() -> None:
    requests: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, request.url.path, body))
        if request.url.path.endswith("/query"):
            return httpx.Response(200, json={"results": [], "has_more": False})
        if request.url.path == "/v1/pages":
            return httpx.Response(200, json={"id": "page-1", "url": "https://notion.so/page-1"})
        if request.url.path.endswith("/children"):
            return httpx.Response(200, json={"results": []})
        return httpx.Response(404, json={"message": "unexpected"})

    client = httpx.Client(base_url="https://api.notion.com", transport=httpx.MockTransport(handler))
    publisher = NotionPublisher(
        NotionPublishSettings(token="test", data_source_id="ds-1"), client=client
    )
    result = publisher.publish(_document())
    assert result.status == "created"
    create = next(
        body for method, path, body in requests if method == "POST" and path == "/v1/pages"
    )
    assert create["parent"]["data_source_id"] == "ds-1"
    assert create["properties"]["유형"]["select"]["name"] == "일간"


def test_notion_publish_refuses_unmanaged_collision() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/query"):
            return httpx.Response(200, json={"results": [{"id": "page-1"}]})
        if request.url.path.endswith("/children"):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "block-1",
                            "type": "paragraph",
                            "paragraph": {"rich_text": [{"plain_text": "user content"}]},
                        }
                    ],
                    "has_more": False,
                },
            )
        return httpx.Response(404)

    client = httpx.Client(base_url="https://api.notion.com", transport=httpx.MockTransport(handler))
    publisher = NotionPublisher(
        NotionPublishSettings(token="test", data_source_id="ds-1"), client=client
    )
    with pytest.raises(NotionApiError, match="refusing overwrite"):
        publisher.publish(_document())


def test_notion_query_retries_retry_after() -> None:
    calls = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0.25"}, json={})
        return httpx.Response(200, json={"results": [], "has_more": False})

    client = httpx.Client(base_url="https://api.notion.com", transport=httpx.MockTransport(handler))
    publisher = NotionPublisher(
        NotionPublishSettings(token="test", data_source_id="ds-1"),
        client=client,
        sleeper=delays.append,
    )
    assert publisher._query_existing("title", "2026-08-16") == []
    assert calls == 2
    assert delays == [0.25]
