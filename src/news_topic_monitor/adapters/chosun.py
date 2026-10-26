from __future__ import annotations

from datetime import datetime

from bs4 import BeautifulSoup

from ..models import DiscoveryPage
from .base import (
    SourceAdapter,
    StructureChangedError,
    decode_script_json_assignment,
    parse_xml_feed,
    text_from_content_elements,
)


class ChosunAdapter(SourceAdapter):
    source = "chosun"
    media_group = "general"
    supports_opinion_scan = True
    allowed_discovery_hosts = frozenset({"www.chosun.com"})
    allowed_article_hosts = frozenset({"www.chosun.com"})
    RSS_URL = "https://www.chosun.com/arc/outboundfeeds/rss/?outputType=xml"
    SITEMAP_URL = "https://www.chosun.com/arc/outboundfeeds/news-sitemap/?outputType=xml"
    # robots.txt가 안내하는 분야별 색인(/arc/outboundfeeds/sitemap/section/index/)의 9개 분야.
    # 전체 news-sitemap과 RSS는 최근 100건 안팎만 보여, 하루 수백 건(일본어·영어판 포함)을
    # 내는 조선일보를 3시간 간격으로 따라가지 못한다(2026-10-19~25 대조: 국내 분야 목록 875건 중
    # 1차 저장분 321건). 2026-10-26 공식 사이트로 구조를 확인했다.
    CATEGORY_SITEMAP_URLS = tuple(
        f"https://www.chosun.com/arc/outboundfeeds/news-sitemap/category/{name}/?outputType=xml"
        for name in (
            "national",
            "politics",
            "economy",
            "opinion",
            "international",
            "medical",
            "culture-life",
            "sports",
            "entertainments",
        )
    )

    def initial_discovery_urls(self, start: datetime, end: datetime) -> list[str]:
        del start, end
        return [self.RSS_URL, self.SITEMAP_URL, *self.CATEGORY_SITEMAP_URLS]

    def parse_discovery(self, content: bytes, url: str) -> DiscoveryPage:
        del url
        return parse_xml_feed(content, self.source)

    def extract_body(self, html_text: str, url: str) -> str:
        del url
        soup = BeautifulSoup(html_text, "html.parser")
        script = soup.select_one("script#fusion-metadata")
        if not script:
            raise StructureChangedError("script#fusion-metadata not found")
        data = decode_script_json_assignment(
            script.string or script.get_text(), "Fusion.globalContent"
        )
        body = text_from_content_elements(data.get("content_elements"))
        if not body:
            raise StructureChangedError("Fusion.globalContent.content_elements contained no text")
        return body
