from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from ..models import ArticleDiscovery, DiscoveryPage
from ..utils import KST, normalize_text, short_text
from .base import StructureChangedError, parse_xml_feed
from .syndication import XmlSyndicationAdapter

# Verified against the live site on 2026-10-31. ``sitemap.xml`` and ``rss/allArticle.xml`` omit
# the newest articles for hours: articles 234303 and 234304 (registered after 234302, shown with
# a 2026-10-30 17:00 KST publication time) were absent from both at 07:41 KST the next day while
# ``/news/articleList.html?view_type=sm`` listed them (robots.txt allows it). The list page
# shows the latest 20 articles, so it is a complement to the sitemap for the newest items, not
# a replacement. The ``page`` query parameter returned the same 20 items for pages 1-3, so only
# the first page is read. A list item is
# ``<li><h4 class="titles"><a href="/news/articleView.html?idxno=N">title</a></h4>
# <p class="lead">...</p><span class="byline"><em>section</em><em>reporter</em>
# <em>2026.10.30 17:00</em></span></li>``; a sample ``<li>`` without an article link is
# markup for a client-side template and is skipped.
LIST_DATETIME = re.compile(r"(\d{4})\.(\d{1,2})\.(\d{1,2})\s+(\d{1,2}):(\d{2})")


class AblenewsAdapter(XmlSyndicationAdapter):
    source = "ablenews"
    media_group = "disability_press"
    allowed_discovery_hosts = frozenset({"www.ablenews.co.kr"})
    allowed_article_hosts = frozenset({"www.ablenews.co.kr"})
    SITEMAP_URL = "https://www.ablenews.co.kr/sitemap.xml"
    LIST_URL = "https://www.ablenews.co.kr/news/articleList.html?view_type=sm"
    discovery_urls = (SITEMAP_URL, LIST_URL)
    body_selector = "#article-view-content-div"

    def parse_discovery(self, content: bytes, url: str) -> DiscoveryPage:
        if url == self.LIST_URL:
            return self._parse_list(content)
        return parse_xml_feed(content, self.source)

    def _parse_list(self, content: bytes) -> DiscoveryPage:
        soup = BeautifulSoup(content, "html.parser")
        items = [item for item in soup.select("li") if item.select_one("h4.titles a[href]")]
        if not items:
            raise StructureChangedError("ablenews list: li with h4.titles a[href] not found")
        articles: list[ArticleDiscovery] = []
        for item in items:
            link = item.select_one("h4.titles a[href]")
            ids = parse_qs(urlsplit(str(link.get("href"))).query).get("idxno") if link else None
            title = normalize_text(link.get_text(" ", strip=True)) if link else ""
            if not ids or not ids[0].isdigit() or not title:
                continue
            fields = [
                normalize_text(node.get_text(" ", strip=True))
                for node in item.select("span.byline em")
            ]
            published_at = next(
                (parsed for parsed in map(parse_list_datetime, reversed(fields)) if parsed), None
            )
            lead = item.select_one("p.lead")
            try:
                articles.append(
                    ArticleDiscovery(
                        source=self.source,
                        article_id=ids[0],
                        canonical_url=f"https://www.ablenews.co.kr/news/articleView.html?idxno={ids[0]}",
                        title=title,
                        section=fields[0] if len(fields) >= 2 else None,
                        published_at=published_at,
                        summary=short_text(lead.get_text(" ", strip=True)) if lead else None,
                    )
                )
            except ValueError:
                continue
        if not articles:
            raise StructureChangedError("ablenews list contained no parseable articles")
        return DiscoveryPage(articles=articles)


def parse_list_datetime(value: str) -> datetime | None:
    """Parse the list's KST display time, e.g. ``2026.10.30 17:00``."""

    match = LIST_DATETIME.fullmatch(value.strip())
    if not match:
        return None
    year, month, day, hour, minute = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day, hour, minute, tzinfo=KST)
    except ValueError:
        return None
