from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .briefing import BriefingDocument, BriefingIssue, render_briefing_markdown
from .sources import SOURCE_LABELS
from .storage import JsonlStorage
from .utils import kst_display, short_error, short_text

NOTION_VERSION = "2026-03-11"
BRIEFING_TITLE_FRAGMENT = "일간 장애정책·노동 뉴스 브리핑"


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
    version: int
    fingerprint: str


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

    def publish(
        self,
        document: BriefingDocument,
    ) -> NotionPublishResult:
        fingerprint = briefing_fingerprint(document)
        dated_pages = self._query_date(self.settings.data_source_id, document.report_date)
        briefing_pages = [
            page for page in dated_pages if BRIEFING_TITLE_FRAGMENT in _page_title(page)
        ]
        version = (
            max((_briefing_version(_page_title(page)) for page in briefing_pages), default=0) + 1
        )
        title = f"GitHub 자동발행 v{version} · {BRIEFING_TITLE_FRAGMENT} ({document.report_date})"
        properties = _page_properties(title, document)
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
            status="created",
            page_id=page_id,
            page_url=str(page_url) if page_url else None,
            created=True,
            version=version,
            fingerprint=fingerprint,
        )

    def record_report(self, document: BriefingDocument, result: NotionPublishResult) -> str | None:
        if not self.settings.reports_data_source_id:
            return None
        title = f"GitHub 브리핑 보고사항 v{result.version} ({document.report_date})"
        matches = self._query_exact(
            self.settings.reports_data_source_id, title, document.report_date
        )
        if matches:
            page_url = matches[0].get("url")
            return str(page_url) if page_url else None
        children = [
            _paragraph(
                f"자동 발행 브리핑 v{result.version}",
                href=result.page_url,
            ),
            _heading("편집·분류 기록", 2),
        ]
        children.extend(
            _bullet(note) for note in (document.editorial_notes or ["별도 제외·이동 기록 없음"])
        )
        children.append(_heading("출처 점검", 2))
        children.extend(
            _bullet(failure)
            for failure in (document.source_failures or ["최근 수집 health에 실패 출처 없음"])
        )
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
                    "날짜": {"date": {"start": document.report_date}},
                },
                "children": children,
            },
        )
        return str(page.get("url")) if page.get("url") else None

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

    def _query_date(self, data_source_id: str, report_date: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            body: dict[str, Any] = {
                "filter": {"property": "날짜", "date": {"equals": report_date}},
                "page_size": 100,
            }
            if cursor:
                body["start_cursor"] = cursor
            payload = self._request(
                "POST",
                f"/data_sources/{data_source_id}/query",
                json=body,
            )
            results.extend(item for item in payload.get("results", []) if isinstance(item, dict))
            if not payload.get("has_more"):
                return results
            cursor = payload.get("next_cursor")
            if not cursor:
                raise NotionApiError("Notion pagination reported has_more without next_cursor")

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
    del crpd_url
    blocks: list[dict[str, Any]] = [_paragraph(document.overview)]
    for section in document.sections:
        if section.title == "IV. 주요 칼럼" and not section.issues:
            continue
        blocks.append(_heading(section.title, 1))
        for index, issue in enumerate(section.issues, start=1):
            blocks.extend(_issue_blocks(index, issue))
    return blocks


def write_notion_health(
    root: Path,
    *,
    report_date: str,
    status: str,
    page_url: str | None = None,
    error: str | None = None,
    include_page_url: bool = False,
    fingerprint: str | None = None,
    version: int | None = None,
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
            "fingerprint": fingerprint,
            "version": version,
        },
    )


def _issue_blocks(index: int, issue: BriefingIssue) -> list[dict[str, Any]]:
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
    blocks = [
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
        _heading("기사 요약", 3),
        _paragraph(issue.summary),
        _heading("보도 논조", 3),
        _paragraph(issue.tone_analysis),
    ]
    if issue.previous_coverage:
        previous_rows = [
            _table_row(
                [[_rich_text("시점")], [_rich_text("비교 자료")], [_rich_text("주요 내용·비교점")]]
            )
        ]
        for item in issue.previous_coverage:
            previous_rows.append(
                _table_row(
                    [
                        [_rich_text(item.published)],
                        [_rich_text(item.label, href=item.url)],
                        [_rich_text(item.comparison)],
                    ]
                )
            )
        blocks.extend(
            [
                _heading("이전 보도 참고", 3),
                _table(previous_rows, width=3),
            ]
        )
    reference_rows = [
        _table_row([[_rich_text("범주")], [_rich_text("자료")], [_rich_text("확인 쟁점")]])
    ]
    for reference in issue.references:
        reference_rows.append(
            _table_row(
                [
                    [_rich_text(reference.category)],
                    [_rich_text(reference.label, href=reference.url)],
                    [_rich_text(reference.note)],
                ]
            )
        )
    if not issue.references:
        reference_rows.append(
            _table_row(
                [
                    [_rich_text("참고 자료")],
                    [_rich_text("확인된 추가 자료 없음")],
                    [_rich_text("후속 조사 필요")],
                ]
            )
        )
    blocks.append(
        {
            "object": "block",
            "type": "toggle",
            "toggle": {
                "rich_text": [_rich_text("추가 자료 · 더 알아보기")],
                "children": [_table(reference_rows, width=3)],
            },
        }
    )
    return blocks


def _page_properties(title: str, document: BriefingDocument) -> dict[str, Any]:
    summary = short_text(document.telegram_summary, 1000) or ""
    return {
        "이름": {"title": [_rich_text(title)]},
        "날짜": {"date": {"start": document.report_date}},
        "유형": {"select": {"name": "일간"}},
        "텔레그램 발송": {"checkbox": False},
        "텔레그램 요약": {"rich_text": [_rich_text(summary)]},
    }


def _rich_text(content: str, href: str | None = None) -> dict[str, Any]:
    text: dict[str, Any] = {"content": content[:2000]}
    if href:
        text["link"] = {"url": href}
    return {"type": "text", "text": text, "annotations": {}}


def _paragraph(content: str, *, href: str | None = None) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [_rich_text(content, href=href)]},
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


def _table(rows: list[dict[str, Any]], *, width: int) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": width,
            "has_column_header": True,
            "has_row_header": False,
            "children": rows,
        },
    }


def briefing_fingerprint(document: BriefingDocument) -> str:
    payload = render_briefing_markdown(document, crpd_url=None).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _page_title(page: dict[str, Any]) -> str:
    title_items = page.get("properties", {}).get("이름", {}).get("title", [])
    return "".join(
        str(item.get("plain_text") or item.get("text", {}).get("content", ""))
        for item in title_items
        if isinstance(item, dict)
    )


def _briefing_version(title: str) -> int:
    match = re.search(r"\bv(\d+)\b", title, re.IGNORECASE)
    return int(match.group(1)) if match else 1


def _notion_retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After", "").strip()
    if retry_after:
        try:
            return min(max(float(retry_after), 0.0), 120.0)
        except ValueError:
            pass
    return min(2**attempt, 4)
