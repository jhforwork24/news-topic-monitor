from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .briefing import BriefingDocument, BriefingIssue
from .sources import SOURCE_LABELS
from .storage import JsonlStorage
from .utils import kst_display, short_error, short_text

NOTION_VERSION = "2026-03-11"
MANAGED_MARKER = "KCILNewsMonitor managed publication"


class NotionConfigurationError(ValueError):
    pass


class NotionApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class NotionPublishSettings:
    token: str
    data_source_id: str
    crpd_reference_url: str | None = None
    reports_data_source_id: str | None = None

    @classmethod
    def from_env(cls) -> NotionPublishSettings:
        token = os.getenv("NOTION_TOKEN", "").strip()
        data_source_id = os.getenv("NOTION_DATA_SOURCE_ID", "").strip()
        if not token:
            raise NotionConfigurationError("NOTION_TOKEN is required for Notion publication")
        if not data_source_id:
            raise NotionConfigurationError(
                "NOTION_DATA_SOURCE_ID is required for Notion publication"
            )
        return cls(
            token=token,
            data_source_id=data_source_id.removeprefix("collection://"),
            crpd_reference_url=os.getenv("NOTION_CRPD_REFERENCE_URL") or None,
            reports_data_source_id=(
                os.getenv("NOTION_REPORTS_DATA_SOURCE_ID", "").strip().removeprefix("collection://")
                or None
            ),
        )


@dataclass(frozen=True)
class NotionPublishResult:
    status: str
    page_id: str
    page_url: str | None
    created: bool


class NotionPublisher:
    def __init__(
        self,
        settings: NotionPublishSettings,
        *,
        client: httpx.Client | None = None,
        sleeper=time.sleep,
        clock=time.monotonic,
    ) -> None:
        self.settings = settings
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url="https://api.notion.com",
            headers={
                "Authorization": f"Bearer {settings.token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
        self.sleeper = sleeper
        self.clock = clock
        self.request_interval_seconds = 0.0 if client is not None else 0.35
        self._last_request: float | None = None

    def __enter__(self) -> NotionPublisher:
        return self

    def __exit__(self, *args: object) -> None:
        if self._owns_client:
            self.client.close()

    def publish(self, document: BriefingDocument) -> NotionPublishResult:
        title = f"GitHub 자동발행 · 일간 장애정책·노동 뉴스 브리핑 ({document.report_date})"
        matches = self._query_existing(title, document.report_date)
        if len(matches) > 1:
            raise NotionApiError("multiple exact-title managed page candidates found")
        properties = _page_properties(title, document)
        created = not matches
        if matches:
            page = matches[0]
            page_id = str(page["id"])
            blocks = self._list_children(page_id)
            if not _has_managed_marker(blocks):
                raise NotionApiError(
                    "matching page is not marked as KCILNewsMonitor-managed; refusing overwrite"
                )
            self._request("PATCH", f"/pages/{page_id}", json={"properties": properties})
            for block in blocks:
                self._request("PATCH", f"/blocks/{block['id']}", json={"in_trash": True})
            page_url = page.get("url")
        else:
            page = self._request(
                "POST",
                "/pages",
                json={
                    "parent": {
                        "type": "data_source_id",
                        "data_source_id": self.settings.data_source_id,
                    },
                    "properties": properties,
                },
            )
            page_id = str(page["id"])
            page_url = page.get("url")
        blocks = notion_blocks(document, crpd_url=self.settings.crpd_reference_url)
        for start in range(0, len(blocks), 100):
            self._request(
                "PATCH",
                f"/blocks/{page_id}/children",
                json={"children": blocks[start : start + 100]},
            )
        return NotionPublishResult(
            status="created" if created else "updated",
            page_id=page_id,
            page_url=str(page_url) if page_url else None,
            created=created,
        )

    def record_failure(self, report_date: str, message: str) -> str | None:
        if not self.settings.reports_data_source_id:
            return None
        title = f"GitHub 브리핑 자동발행 실패 ({report_date})"
        matches = self._query_exact(self.settings.reports_data_source_id, title, report_date)
        if matches:
            page_url = matches[0].get("url")
            return str(page_url) if page_url else None
        page = self._request(
            "POST",
            "/pages",
            json={
                "parent": {
                    "type": "data_source_id",
                    "data_source_id": self.settings.reports_data_source_id,
                },
                "properties": {
                    "이름": {"title": [_rich_text(title)]},
                    "날짜": {"date": {"start": report_date}},
                },
                "children": [
                    _paragraph(
                        "GitHub Actions 자동발행 점검 필요: "
                        + (short_error(message) or "상세 오류 없음")
                    )
                ],
            },
        )
        return str(page.get("url")) if page.get("url") else None

    def _query_existing(self, title: str, report_date: str) -> list[dict[str, Any]]:
        return self._query_exact(self.settings.data_source_id, title, report_date)

    def _query_exact(
        self, data_source_id: str, title: str, report_date: str
    ) -> list[dict[str, Any]]:
        payload = self._request(
            "POST",
            f"/data_sources/{data_source_id}/query",
            json={
                "filter": {
                    "and": [
                        {"property": "이름", "title": {"equals": title}},
                        {"property": "날짜", "date": {"equals": report_date}},
                    ]
                },
                "page_size": 10,
            },
        )
        results = payload.get("results", [])
        return [item for item in results if isinstance(item, dict)]

    def _list_children(self, block_id: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            payload = self._request("GET", f"/blocks/{block_id}/children", params=params)
            results.extend(item for item in payload.get("results", []) if isinstance(item, dict))
            if not payload.get("has_more"):
                return results
            cursor = payload.get("next_cursor")
            if not cursor:
                raise NotionApiError("Notion pagination reported has_more without next_cursor")

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        api_path = f"/v1{path}"
        response: httpx.Response | None = None
        for attempt in range(3):
            self._wait_for_rate_limit()
            try:
                response = self.client.request(method, api_path, **kwargs)
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise NotionApiError(f"Notion API request failed: {exc}") from exc
                self.sleeper(min(2**attempt, 4))
                continue
            if response.status_code not in {429, 500, 502, 503, 504}:
                break
            if attempt < 2:
                self.sleeper(_notion_retry_delay(response, attempt))
        assert response is not None
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = short_text(exc.response.text, 500) or "no response body"
            raise NotionApiError(
                f"Notion API {method} {path} returned HTTP {exc.response.status_code}: {detail}"
            ) from exc
        payload = response.json()
        if not isinstance(payload, dict):
            raise NotionApiError("Notion API returned a non-object JSON response")
        return payload

    def _wait_for_rate_limit(self) -> None:
        now = self.clock()
        if self._last_request is not None:
            delay = self.request_interval_seconds - (now - self._last_request)
            if delay > 0:
                self.sleeper(delay)
                now = self.clock()
        self._last_request = now


def notion_blocks(document: BriefingDocument, *, crpd_url: str | None) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        _paragraph(document.overview),
        {
            "object": "block",
            "type": "callout",
            "callout": {
                "icon": {"type": "emoji", "emoji": "🤖"},
                "rich_text": [_rich_text(MANAGED_MARKER)],
            },
        },
    ]
    for section in document.sections:
        blocks.append(_heading(section.title, 1))
        if not section.issues:
            blocks.append(
                _paragraph(
                    "공개 발견 경로와 현재 판별 규칙에서 선정된 기사가 없다. "
                    "수집 실패를 기사 부재로 해석해서는 안 된다."
                )
            )
            continue
        for index, issue in enumerate(section.issues, start=1):
            blocks.extend(_issue_blocks(index, issue, crpd_url=crpd_url))
    blocks.extend([_heading("점검", 1)])
    if document.source_failures:
        blocks.append(
            _paragraph("다음 출처는 실패·차단 상태이므로 해당 매체의 기사 부재를 뜻하지 않는다.")
        )
        blocks.extend(_bullet(failure) for failure in document.source_failures)
    else:
        blocks.append(_paragraph("최근 건강상태에서 출처별 실패가 기록되지 않았다."))
    blocks.append(
        _paragraph("본문 원문은 저장하지 않았으며 제목·URL·공개 요약·판정 근거만 사용하였다.")
    )
    return blocks


def write_notion_health(
    root: Path,
    *,
    report_date: str,
    status: str,
    page_url: str | None = None,
    error: str | None = None,
    include_page_url: bool = False,
) -> None:
    JsonlStorage.atomic_write_json(
        root / "health" / "notion" / "latest.json",
        {
            "report_date": report_date,
            "checked_at": datetime.now().astimezone().isoformat(),
            "status": status,
            # Default to status-only health because this repository can be public
            # while the target Notion database is private.
            "page_url": page_url if include_page_url else None,
            "error": short_error(error),
        },
    )


def _issue_blocks(
    index: int, issue: BriefingIssue, *, crpd_url: str | None
) -> list[dict[str, Any]]:
    rows = [_table_row([[_rich_text("언론사")], [_rich_text("기사")], [_rich_text("발행")]])]
    for article in issue.articles:
        rows.append(
            _table_row(
                [
                    [_rich_text(SOURCE_LABELS.get(article.source, article.source))],
                    [_rich_text(article.title, href=article.canonical_url)],
                    [_rich_text(kst_display(article.published_at))],
                ]
            )
        )
    references = [_bullet(reference) for reference in issue.references]
    if issue.crpd_articles:
        label = "CRPD 조문별 통합참조표: " + ", ".join(issue.crpd_articles)
        references.append(_bullet(label, href=crpd_url))
    return [
        _heading(f"{index}. {issue.title}", 2),
        _heading("주요 언론 보도", 3),
        {
            "object": "block",
            "type": "table",
            "table": {
                "table_width": 3,
                "has_column_header": True,
                "has_row_header": False,
                "children": rows,
            },
        },
        _heading("오늘의 변화", 3),
        _paragraph(issue.assessment),
        {
            "object": "block",
            "type": "toggle",
            "toggle": {
                "rich_text": [_rich_text("추가 자료 · 더 찾아보기")],
                "children": references or [_paragraph("추가 자료 없음")],
            },
        },
    ]


def _page_properties(title: str, document: BriefingDocument) -> dict[str, Any]:
    summary = short_text(document.overview, 1000) or ""
    return {
        "이름": {"title": [_rich_text(title)]},
        "날짜": {"date": {"start": document.report_date}},
        "유형": {"select": {"name": "일간"}},
        "텔레그램 발송": {"checkbox": False},
        "텔레그램 요약": {"rich_text": [_rich_text(summary)]},
    }


def _has_managed_marker(blocks: list[dict[str, Any]]) -> bool:
    if not blocks:
        return False
    for block in blocks[:3]:
        body = block.get(block.get("type", ""), {})
        rich = body.get("rich_text", []) if isinstance(body, dict) else []
        if any(
            MANAGED_MARKER in str(item.get("plain_text", item.get("text", {}).get("content", "")))
            for item in rich
            if isinstance(item, dict)
        ):
            return True
    return False


def _rich_text(content: str, href: str | None = None) -> dict[str, Any]:
    text: dict[str, Any] = {"content": content[:2000]}
    if href:
        text["link"] = {"url": href}
    return {"type": "text", "text": text, "annotations": {}}


def _paragraph(content: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [_rich_text(content)]},
    }


def _heading(content: str, level: int) -> dict[str, Any]:
    block_type = f"heading_{level}"
    return {
        "object": "block",
        "type": block_type,
        block_type: {"rich_text": [_rich_text(content)]},
    }


def _bullet(content: str, *, href: str | None = None) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [_rich_text(content, href=href)]},
    }


def _table_row(cells: list[list[dict[str, Any]]]) -> dict[str, Any]:
    return {"object": "block", "type": "table_row", "table_row": {"cells": cells}}


def _notion_retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After", "").strip()
    if retry_after:
        try:
            return min(max(float(retry_after), 0.0), 120.0)
        except ValueError:
            pass
    return min(2**attempt, 4)
