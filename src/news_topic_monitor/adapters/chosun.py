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
    allowed_discovery_hosts = frozenset({"www.chosun.com"})
    allowed_article_hosts = frozenset({"www.chosun.com"})
    RSS_URL = "https://www.chosun.com/arc/outboundfeeds/rss/?outputType=xml"
    SITEMAP_URL = "https://www.chosun.com/arc/outboundfeeds/news-sitemap/?outputType=xml"

    def initial_discovery_urls(self, start: datetime, end: datetime) -> list[str]:
        del start, end
        return [self.RSS_URL, self.SITEMAP_URL]

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
