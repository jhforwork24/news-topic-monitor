from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from news_topic_monitor.briefing import BriefingDocument, BriefingSection
from news_topic_monitor.notion_publish import (
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
        telegram_summary="텔레그램 총평",
        sections=[BriefingSection("I. 장애정책·장애인운동", [])],
        source_failures=["참세상: robots.txt 확인 불능"],
        editorial_notes=["단순 홍보 기사 제외"],
    )


def test_notion_blocks_keep_technical_notes_out_of_briefing() -> None:
    blocks = notion_blocks(_document(), crpd_url=None)
    assert blocks[0]["paragraph"]["rich_text"][0]["text"]["content"] == "총평"
    assert any(block["type"] == "heading_1" for block in blocks)
    rendered = json.dumps(blocks, ensure_ascii=False)
    assert "단순 홍보 기사 제외" not in rendered
    assert "참세상" not in rendered
    assert "점검" not in rendered


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
    assert result.version == 1
    create = next(
        body for method, path, body in requests if method == "POST" and path == "/v1/pages"
    )
    assert create["parent"]["data_source_id"] == "ds-1"
    assert create["properties"]["유형"]["select"]["name"] == "일간"
    telegram = create["properties"]["텔레그램 요약"]["rich_text"][0]["text"]["content"]
    assert telegram == "텔레그램 총평"
    assert "주요 칼럼" not in telegram
    title = create["properties"]["이름"]["title"][0]["text"]["content"]
    assert title.startswith("GitHub 자동발행 v1")


def test_notion_publish_creates_next_version_instead_of_overwriting() -> None:
    requests: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, request.url.path, body))
        if request.url.path.endswith("/query"):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "manual-v3",
                            "url": "https://notion.so/manual-v3",
                            "properties": {
                                "이름": {
                                    "title": [
                                        {
                                            "plain_text": (
                                                "편집 검수판 v3 · 일간 장애정책·노동 뉴스 "
                                                "브리핑 (2026-08-16)"
                                            )
                                        }
                                    ]
                                }
                            },
                        }
                    ],
                    "has_more": False,
                },
            )
        if request.url.path == "/v1/pages":
            return httpx.Response(200, json={"id": "auto-v4", "url": "https://notion.so/auto-v4"})
        if request.url.path.endswith("/children"):
            return httpx.Response(200, json={"results": []})
        return httpx.Response(404, json={"message": "unexpected"})

    client = httpx.Client(base_url="https://api.notion.com", transport=httpx.MockTransport(handler))
    publisher = NotionPublisher(
        NotionPublishSettings(token="test", data_source_id="ds-1"), client=client
    )
    result = publisher.publish(_document())
    assert result.status == "created"
    assert result.version == 4
    create = next(
        body for method, path, body in requests if method == "POST" and path == "/v1/pages"
    )
    title = create["properties"]["이름"]["title"][0]["text"]["content"]
    assert title.startswith("GitHub 자동발행 v4")
    assert not any(
        method == "PATCH" and path.startswith("/v1/blocks/manual-v3")
        for method, path, _body in requests
    )


def test_identical_rerun_creates_next_version_without_overwriting() -> None:
    requests: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, request.url.path, body))
        if request.url.path.endswith("/query"):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "auto-v4",
                            "url": "https://notion.so/auto-v4",
                            "properties": {
                                "이름": {
                                    "title": [
                                        {
                                            "plain_text": (
                                                "GitHub 자동발행 v4 · 일간 장애정책·노동 뉴스 "
                                                "브리핑 (2026-08-16)"
                                            )
                                        }
                                    ]
                                }
                            },
                        }
                    ],
                    "has_more": False,
                },
            )
        if request.url.path == "/v1/pages":
            return httpx.Response(200, json={"id": "auto-v5", "url": "https://notion.so/auto-v5"})
        if request.url.path.endswith("/children"):
            return httpx.Response(200, json={"results": []})
        return httpx.Response(404, json={"message": "unexpected"})

    client = httpx.Client(base_url="https://api.notion.com", transport=httpx.MockTransport(handler))
    publisher = NotionPublisher(
        NotionPublishSettings(token="test", data_source_id="ds-1"), client=client
    )
    result = publisher.publish(_document())
    assert result.status == "created"
    assert result.version == 5
    assert any(method == "POST" and path == "/v1/pages" for method, path, _ in requests)
    assert not any(
        method == "PATCH" and path.startswith("/v1/blocks/auto-v4")
        for method, path, _body in requests
    )


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
    assert publisher._query_date("ds-1", "2026-08-16") == []
    assert calls == 2
    assert delays == [0.25]
