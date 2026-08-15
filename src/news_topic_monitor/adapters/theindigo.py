from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from ..models import ArticleDiscovery, DiscoveryPage
from ..utils import normalize_text, normalize_url, parse_datetime, short_text
from .base import SourceAdapter, StructureChangedError


class TheindigoAdapter(SourceAdapter):
    source = "theindigo"
    media_group = "disability_press"
    allowed_discovery_hosts = frozenset({"theindigo.co.kr"})
    allowed_article_hosts = frozenset({"theindigo.co.kr"})

    def initial_discovery_urls(self, start: datetime, end: datetime) -> list[str]:
        query = urlencode(
            {
                "per_page": 100,
                "after": _wp_time(start),
                "before": _wp_time(end),
                "orderby": "date",
                "order": "desc",
                "_fields": "id,date_gmt,modified_gmt,link,title,excerpt,categories",
            }
        )
        return [f"https://theindigo.co.kr/wp-json/wp/v2/posts?{query}"]

    def parse_discovery(self, content: bytes, url: str) -> DiscoveryPage:
        del url
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise StructureChangedError(f"invalid WordPress JSON: {exc}") from exc
        if not isinstance(payload, list):
            raise StructureChangedError("WordPress posts response is not an array")
        if not payload:
            return DiscoveryPage()
        articles: list[ArticleDiscovery] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            title = _rendered(item.get("title"))
            link = item.get("link")
            if not title or not link:
                continue
            try:
                articles.append(
                    ArticleDiscovery(
                        source=self.source,
                        article_id=str(item.get("id") or "") or None,
                        canonical_url=normalize_url(str(link)),
                        title=normalize_text(BeautifulSoup(title, "html.parser").get_text(" ")),
                        published_at=parse_datetime(str(item["date_gmt"]) + "Z")
                        if item.get("date_gmt")
                        else None,
                        updated_at=parse_datetime(str(item["modified_gmt"]) + "Z")
                        if item.get("modified_gmt")
                        else None,
                        summary=short_text(
                            BeautifulSoup(_rendered(item.get("excerpt")), "html.parser").get_text(
                                " ", strip=True
                            )
                        ),
                    )
                )
            except ValueError:
                continue
        if not articles:
            raise StructureChangedError("WordPress response contained no parseable posts")
        return DiscoveryPage(articles=articles)

    def extract_body(self, html_text: str, url: str) -> str:
        del url
        soup = BeautifulSoup(html_text, "html.parser")
        node = soup.select_one(".td-post-content")
        if not node:
            raise StructureChangedError(".td-post-content not found")
        text = node.get_text("\n", strip=True)
        if not text:
            raise StructureChangedError(".td-post-content was empty")
        return text


def _rendered(value: object) -> str:
    if isinstance(value, dict):
        rendered = value.get("rendered")
        return str(rendered) if rendered else ""
    return ""


def _wp_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
