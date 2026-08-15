from __future__ import annotations

from datetime import datetime

from bs4 import BeautifulSoup

from ..models import DiscoveryPage
from .base import SourceAdapter, StructureChangedError, parse_xml_feed


class XmlSyndicationAdapter(SourceAdapter):
    discovery_urls: tuple[str, ...]
    body_selector: str | None

    def initial_discovery_urls(self, start: datetime, end: datetime) -> list[str]:
        del start, end
        return list(self.discovery_urls)

    def parse_discovery(self, content: bytes, url: str) -> DiscoveryPage:
        del url
        return parse_xml_feed(content, self.source)

    def extract_body(self, html_text: str, url: str) -> str:
        del url
        if not self.body_selector:
            raise StructureChangedError(
                f"{self.source}: a verified server-rendered body selector is unavailable"
            )
        soup = BeautifulSoup(html_text, "html.parser")
        node = soup.select_one(self.body_selector)
        if not node:
            raise StructureChangedError(f"{self.body_selector} not found")
        text = node.get_text("\n", strip=True)
        if not text:
            raise StructureChangedError(f"{self.body_selector} was empty")
        return text


class FailClosedAdapter(SourceAdapter):
    """Adapter whose official origin is intentionally probed only through robots.txt."""

    discovery_url: str

    def initial_discovery_urls(self, start: datetime, end: datetime) -> list[str]:
        del start, end
        return [self.discovery_url]

    def parse_discovery(self, content: bytes, url: str) -> DiscoveryPage:
        del content, url
        raise StructureChangedError("fail-closed source unexpectedly returned discovery content")

    def extract_body(self, html_text: str, url: str) -> str:
        del html_text, url
        raise StructureChangedError("fail-closed source has no body parser")
