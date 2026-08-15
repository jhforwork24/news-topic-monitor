from __future__ import annotations

from datetime import datetime, timedelta

from bs4 import BeautifulSoup

from ..models import DiscoveryPage
from ..utils import KST
from .base import SourceAdapter, StructureChangedError, parse_xml_feed


class JoongangAdapter(SourceAdapter):
    source = "joongang"
    media_group = "general"
    supports_opinion_scan = True
    allowed_discovery_hosts = frozenset({"www.joongang.co.kr"})
    allowed_article_hosts = frozenset({"www.joongang.co.kr"})
    LATEST_URL = "https://www.joongang.co.kr/sitemap/latest-articles"
    DATE_URL = "https://www.joongang.co.kr/sitemap/articles/{year}/{date}"

    def initial_discovery_urls(self, start: datetime, end: datetime) -> list[str]:
        dates: list[str] = []
        cursor = start.astimezone(KST).date()
        last = (end - timedelta(microseconds=1)).astimezone(KST).date()
        while cursor <= last:
            dates.append(self.DATE_URL.format(year=cursor.year, date=cursor.strftime("%Y%m%d")))
            cursor += timedelta(days=1)
        return [self.LATEST_URL, *dates]

    def parse_discovery(self, content: bytes, url: str) -> DiscoveryPage:
        del url
        return parse_xml_feed(content, self.source)

    def extract_body(self, html_text: str, url: str) -> str:
        del url
        soup = BeautifulSoup(html_text, "html.parser")
        node = soup.select_one("#article_body")
        if not node:
            raise StructureChangedError("#article_body not found")
        text = node.get_text("\n", strip=True)
        if not text:
            raise StructureChangedError("#article_body was empty")
        return text
