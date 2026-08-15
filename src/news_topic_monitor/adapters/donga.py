from __future__ import annotations

from datetime import datetime

from bs4 import BeautifulSoup

from ..models import DiscoveryPage
from .base import SourceAdapter, StructureChangedError, parse_xml_feed


class DongaAdapter(SourceAdapter):
    source = "donga"
    media_group = "general"
    supports_opinion_scan = True
    allowed_discovery_hosts = frozenset({"rss.donga.com", "www.donga.com"})
    allowed_article_hosts = frozenset({"www.donga.com"})
    RSS_URL = "https://rss.donga.com/total.xml"
    SITEMAP_URL = "https://www.donga.com/sitemap/donga-newsmap.xml"

    def initial_discovery_urls(self, start: datetime, end: datetime) -> list[str]:
        del start, end
        return [self.RSS_URL, self.SITEMAP_URL]

    def parse_discovery(self, content: bytes, url: str) -> DiscoveryPage:
        del url
        return parse_xml_feed(content, self.source)

    def extract_body(self, html_text: str, url: str) -> str:
        del url
        soup = BeautifulSoup(html_text, "html.parser")
        node = soup.select_one(".news_view")
        if not node:
            raise StructureChangedError(".news_view not found")
        text = node.get_text("\n", strip=True)
        if not text:
            raise StructureChangedError(".news_view was empty")
        return text
