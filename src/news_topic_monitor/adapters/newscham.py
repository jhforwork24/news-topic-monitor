from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from ..models import ArticleDiscovery, DiscoveryPage
from ..utils import KST, normalize_text, short_text
from .base import SourceAdapter, StructureChangedError

# Verified against the live site on 2026-10-07 (newscham.net/all-articles/ and an
# /N/ article page). The site moved from www.newscham.net/articles/N to the apex host
# newscham.net (WordPress); the old URLs 301 to /N/. The apex robots.txt exists (200) and
# only disallows /wp/wp-admin/, so no robots.txt opt-in is needed. The list is paged with
# the FacetWP query parameter ``_paged`` and is date-descending. 2026-10-09: the separate
# curation list (/all-articles/curation/, same markup, about one item a day) holds articles
# that never appear in the main list (e.g. 130022, 130013, 130006), so it is a second
# discovery path. It is not under ``date_ordered_list_prefix``, so its pages are always fetched.
ARTICLE_PATH = re.compile(r"^/(\d+)/?$")
LIST_DATETIME = re.compile(r"(\d{4})\.(\d{1,2})\.(\d{1,2})\.?\s+(\d{1,2}):(\d{2})")


class NewschamAdapter(SourceAdapter):
    source = "newscham"
    media_group = "labor_alternative"
    allowed_discovery_hosts = frozenset({"newscham.net"})
    allowed_article_hosts = frozenset({"newscham.net"})
    LIST_URL = "https://newscham.net/all-articles/?_paged={page}"
    CURATION_LIST_URL = "https://newscham.net/all-articles/curation/?_paged={page}"
    date_ordered_list_prefix = "https://newscham.net/all-articles/?_paged="

    def __init__(self, max_pages: int = 20, curation_pages: int = 2) -> None:
        self.max_pages = max_pages
        self.curation_pages = curation_pages

    def initial_discovery_urls(self, start: datetime, end: datetime) -> list[str]:
        del start, end
        return [self.LIST_URL.format(page=page) for page in range(1, self.max_pages + 1)] + [
            self.CURATION_LIST_URL.format(page=page) for page in range(1, self.curation_pages + 1)
        ]

    def parse_discovery(self, content: bytes, url: str) -> DiscoveryPage:
        del url
        soup = BeautifulSoup(content, "html.parser")
        items = soup.select("div.article-loop_default div.gb-loop-item")
        if not items:
            raise StructureChangedError("div.article-loop_default div.gb-loop-item not found")
        articles: list[ArticleDiscovery] = []
        for item in items:
            link = item.select_one("a.link[href]")
            title_node = item.select_one("h3")
            if link is None or title_node is None:
                continue
            match = ARTICLE_PATH.match(urlsplit(str(link.get("href"))).path)
            title = normalize_text(title_node.get_text(" ", strip=True))
            if not match or not title:
                continue
            article_id = match.group(1)
            date_node = item.select_one("div.date")
            published_at = (
                parse_newscham_datetime(date_node.get_text(" ", strip=True)) if date_node else None
            )
            summary = item.select_one("p.summary")
            try:
                articles.append(
                    ArticleDiscovery(
                        source=self.source,
                        article_id=article_id,
                        canonical_url=f"https://newscham.net/{article_id}/",
                        title=title,
                        byline=_text_or_none(item.select_one("div.author")),
                        section=_text_or_none(item.select_one("div.taxonomy a")),
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
        node = soup.select_one("article.post-content div.ep-single-content")
        if not node:
            raise StructureChangedError("article.post-content div.ep-single-content not found")
        text = node.get_text("\n", strip=True)
        if not text:
            raise StructureChangedError("div.ep-single-content was empty")
        return text


def parse_newscham_datetime(value: str) -> datetime | None:
    """Parse the site's KST display time, e.g. ``2026.10.06 9:43``."""

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
