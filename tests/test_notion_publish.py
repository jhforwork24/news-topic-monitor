from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from news_topic_monitor.briefing import (
    BriefingDocument,
    BriefingIssue,
    BriefingReference,
    BriefingSection,
    PreviousCoverage,
)
from news_topic_monitor.models import (
    ArticleRecord,
    BodyStatus,
    Classification,
    VerificationStatus,
)
from news_topic_monitor.notion_publish import (
    NotionConfigurationError,
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


def test_queue_env_targets_the_staging_database_not_reports(monkeypatch) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "test-token")
    monkeypatch.setenv("NOTION_QUEUE_DATA_SOURCE_ID", "collection://queue-ds")
    monkeypatch.setenv("NOTION_REPORTS_DATA_SOURCE_ID", "reports-ds")

    settings = NotionPublishSettings.from_queue_env()

    assert settings.data_source_id == "queue-ds"
    assert settings.reports_data_source_id == "reports-ds"


def test_queue_env_requires_the_queue_data_source_even_when_reports_is_set(
    monkeypatch,
) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "test-token")
    monkeypatch.delenv("NOTION_QUEUE_DATA_SOURCE_ID", raising=False)
    monkeypatch.setenv("NOTION_REPORTS_DATA_SOURCE_ID", "reports-ds")

    with pytest.raises(NotionConfigurationError):
        NotionPublishSettings.from_queue_env()


def test_reports_env_does_not_require_the_queue_data_source(monkeypatch) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "test-token")
    monkeypatch.delenv("NOTION_QUEUE_DATA_SOURCE_ID", raising=False)
    monkeypatch.setenv("NOTION_REPORTS_DATA_SOURCE_ID", "reports-ds")

    settings = NotionPublishSettings.from_reports_env()

    assert settings.reports_data_source_id == "reports-ds"


def test_notion_blocks_keep_technical_notes_out_of_briefing() -> None:
    blocks = notion_blocks(_document(), crpd_url=None)
    assert blocks[0]["paragraph"]["rich_text"][0]["text"]["content"] == "총평"
    assert any(block["type"] == "heading_1" for block in blocks)
    rendered = json.dumps(blocks, ensure_ascii=False)
    assert "단순 홍보 기사 제외" not in rendered
    assert "참세상" not in rendered
    assert "점검" not in rendered


def test_notion_issue_uses_article_bullets_and_one_reference_toggle() -> None:
    now = datetime(2026, 8, 15, 1, tzinfo=UTC)
    article = ArticleRecord(
        source="hani",
        article_id="1",
        canonical_url="https://example.com/article",
        title="장애인 이동권 보장 촉구",
        byline="홍길동 기자",
        section="사회",
        published_at=now,
        first_seen_at=now,
        last_seen_at=now,
        summary="장애인단체가 이동권 보장을 촉구했다.",
        body_status=BodyStatus.FETCHED,
        classification=Classification.RELEVANT,
        topic_score=10.0,
        classification_reason="규칙 판정",
        verification_status=VerificationStatus.BODY_VERIFIED,
    )
    issue = BriefingIssue(
        title=article.title,
        articles=[article],
        summary="장애인단체는 이동권 보장을 요구했다.",
        tone_analysis="한겨레는 당사자 요구를 중심으로 보도했다.",
        previous_coverage=[
            PreviousCoverage(
                "2026-08-14",
                "이전 이동권 보도",
                "https://example.com/previous",
                "정책 변화 여부를 대조한다.",
            )
        ],
        references=[
            BriefingReference(
                "현행 제도",
                "교통약자의 이동편의 증진법",
                "https://example.com/law",
                "법적 의무를 확인한다.",
            )
        ],
    )
    document = _document()
    document.sections = [BriefingSection("I. 장애정책·장애인운동", [issue])]
    blocks = notion_blocks(document, crpd_url=None)
    rendered = json.dumps(blocks, ensure_ascii=False)
    bullets = [block for block in blocks if block["type"] == "bulleted_list_item"]
    assert len(bullets) == 1
    assert "홍길동 기자" in rendered
    assert "이슈 요약·보도 논조" in rendered
    assert "기사 요약" not in rendered
    assert "이전 보도 참고" not in rendered
    assert "KST" not in rendered
    toggle = next(block for block in blocks if block["type"] == "toggle")
    toggle_text = json.dumps(toggle, ensure_ascii=False)
    assert "이전 보도" in toggle_text
    assert "현행 제도" in toggle_text


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


def test_notion_publish_does_not_duplicate_an_existing_manual_briefing() -> None:
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
    assert result.status == "already_published"
    assert result.created is False
    assert result.version == 3
    assert result.page_id == "manual-v3"
    assert not any(method == "POST" and path == "/v1/pages" for method, path, _ in requests)


def test_identical_rerun_returns_existing_page_without_creating_a_version() -> None:
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
    assert result.status == "already_published"
    assert result.created is False
    assert result.version == 4
    assert result.page_id == "auto-v4"
    assert not any(method == "POST" and path == "/v1/pages" for method, path, _ in requests)
    assert not any(
        method == "PATCH" and path.startswith("/v1/blocks/auto-v4")
        for method, path, _body in requests
    )


def test_failure_report_preserves_structured_lines_and_appends_on_retry() -> None:
    requests: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, request.url.path, body))
        if request.url.path.endswith("/query"):
            return httpx.Response(
                200,
                json={
                    "results": [{"id": "failure-page", "url": "https://notion.so/failure"}],
                    "has_more": False,
                },
            )
        if request.method == "PATCH" and request.url.path.endswith("/children"):
            return httpx.Response(200, json={"object": "list", "results": []})
        return httpx.Response(404, json={"message": "unexpected"})

    client = httpx.Client(base_url="https://api.notion.com", transport=httpx.MockTransport(handler))
    publisher = NotionPublisher(
        NotionPublishSettings(
            token="test",
            data_source_id="briefing-ds",
            reports_data_source_id="reports-ds",
        ),
        client=client,
    )
    result = publisher.record_failure(
        "2026-08-20",
        "원인=공식 목록 실패; 대체경로=Naver; 결과=degraded; 다음조치=재수집\n"
        "원인=감사 오류; 대체경로=발행 차단; 결과=failed; 다음조치=재편집",
    )

    assert result == "https://notion.so/failure"
    append = next(
        body
        for method, path, body in requests
        if method == "PATCH" and path == "/v1/blocks/failure-page/children"
    )
    rendered = json.dumps(append, ensure_ascii=False)
    assert "원인=공식 목록 실패" in rendered
    assert "다음조치=재편집" in rendered


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
