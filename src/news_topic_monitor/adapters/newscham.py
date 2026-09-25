from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup

from ..models import ArticleDiscovery, DiscoveryPage
from ..utils import KST, normalize_text, short_text
from .base import SourceAdapter, StructureChangedError

# Verified against the live site on 2026-09-25 (www.newscham.net/articles/ and an
# /articles/N page). The site publishes no RSS or sitemap (/rss/ is empty, /rss.xml
# and /sitemap.xml are 404) and its robots.txt is 404, which the source registry
# reads as "no robots.txt" by an approved per-source opt-in.
ARTICLE_PATH = re.compile(r"^/articles/(\d+)/?(?:\?.*)?$")
LIST_DATETIME = re.compile(r"(\d{4})\.(\d{1,2})\.(\d{1,2})\.?\s+(\d{1,2}):(\d{2})")


class NewschamAdapter(SourceAdapter):
    source = "newscham"
    media_group = "labor_alternative"
    allowed_discovery_hosts = frozenset({"www.newscham.net"})
    allowed_article_hosts = frozenset({"www.newscham.net"})
    LIST_URL = "https://www.newscham.net/articles/?page={page}"
    date_ordered_list_prefix = "https://www.newscham.net/articles/?page="

    def __init__(self, max_pages: int = 20) -> None:
        self.max_pages = max_pages

    def initial_discovery_urls(self, start: datetime, end: datetime) -> list[str]:
        del start, end
        return [self.LIST_URL.format(page=page) for page in range(1, self.max_pages + 1)]

    def parse_discovery(self, content: bytes, url: str) -> DiscoveryPage:
        del url
        soup = BeautifulSoup(content, "html.parser")
        items = soup.select("section.mainContents article.figure")
        if not items:
            raise StructureChangedError("section.mainContents article.figure not found")
        articles: list[ArticleDiscovery] = []
        for item in items:
            link = item.select_one("h3 a[href]")
            if link is None:
                continue
            match = ARTICLE_PATH.match(str(link.get("href")))
            title = normalize_text(link.get_text(" ", strip=True))
            if not match or not title:
                continue
            article_id = match.group(1)
            time_node = item.select_one("time.pubdate")
            published_at = (
                parse_newscham_datetime(
                    str(time_node.get("datetime") or time_node.get_text(" ", strip=True))
                )
                if time_node
                else None
            )
            category = item.select_one("div.category")
            author = item.select_one("address.author")
            summary = item.select_one("p.summary")
            try:
                articles.append(
                    ArticleDiscovery(
                        source=self.source,
                        article_id=article_id,
                        canonical_url=f"https://www.newscham.net/articles/{article_id}",
                        title=title,
                        byline=_text_or_none(author),
                        section=_section(category),
                        published_at=published_at,
                        summary=short_text(summary.get_text(" ", strip=True)) if summary else None,
                    )
                )
            except ValueError:
                continue
        if not articles:
            raise StructureChangedError("newscham article list contained no parseable articles")
        return DiscoveryPage(articles=articles)

    def extract_body(self, html_text: str, url: str) -> str:
        del url
        soup = BeautifulSoup(html_text, "html.parser")
        node = soup.select_one("article#news-article-post div#news-article-content")
        if not node:
            raise StructureChangedError("div#news-article-content not found")
        text = node.get_text("\n", strip=True)
        if not text:
            raise StructureChangedError("div#news-article-content was empty")
        return text


def parse_newscham_datetime(value: str) -> datetime | None:
    """Parse the site's KST display time, e.g. ``2026.09.23. 9:51``."""

    match = LIST_DATETIME.search(value)
    if not match:
        return None
    year, month, day, hour, minute = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day, hour, minute, tzinfo=KST)
    except ValueError:
        return None


def _text_or_none(node: object) -> str | None:
    if node is None:
        return None
    text = normalize_text(node.get_text(" ", strip=True))  # type: ignore[attr-defined]
    return text or None


def _section(node: object) -> str | None:
    text = _text_or_none(node)
    if not text:
        return None
    return text.strip("[] ") or None
