from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ..models import ArticleDiscovery, DiscoveryPage
from ..utils import normalize_text, normalize_url, parse_datetime, short_text
from .base import SourceAdapter, StructureChangedError, parse_xml_feed


class HaniAdapter(SourceAdapter):
    source = "hani"
    allowed_discovery_hosts = frozenset({"www.hani.co.kr"})
    allowed_article_hosts = frozenset({"www.hani.co.kr"})
    LIST_URL = "https://www.hani.co.kr/arti?page={page}"
    RSS_URL = "https://www.hani.co.kr/rss/"

    def __init__(self, max_pages: int = 50) -> None:
        self.max_pages = max_pages

    def initial_discovery_urls(self, start: datetime, end: datetime) -> list[str]:
        del start, end
        return [
            self.RSS_URL,
            *(self.LIST_URL.format(page=page) for page in range(1, self.max_pages + 1)),
        ]

    def parse_discovery(self, content: bytes, url: str) -> DiscoveryPage:
        if urlsplit(url).path.startswith("/rss"):
            return self._parse_rss_path(content, url)
        soup = BeautifulSoup(content, "html.parser")
        script = soup.select_one("script#__NEXT_DATA__")
        if not script:
            raise StructureChangedError("script#__NEXT_DATA__ not found")
        try:
            data = json.loads(script.string or script.get_text())
            raw_list = data["props"]["pageProps"]["list"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise StructureChangedError("props.pageProps.list missing from __NEXT_DATA__") from exc
        if not isinstance(raw_list, list):
            raise StructureChangedError("props.pageProps.list is not an array")
        articles: list[ArticleDiscovery] = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            link = _first(item, "url", "link", "href", "articleUrl", "article_url")
            title = _first(item, "title", "subject", "headline")
            if not link or not title:
                continue
            published = _first(
                item, "createDate", "publishedAt", "published_at", "date", "writeDate"
            )
            updated = _first(item, "updateDate", "updatedAt", "updated_at", "modifyDate")
            section = _section_name(_first(item, "section", "sectionName", "category"))
            try:
                articles.append(
                    ArticleDiscovery(
                        source=self.source,
                        article_id=str(_first(item, "id", "articleId", "article_id") or "") or None,
                        canonical_url=normalize_url(urljoin(url, str(link))),
                        title=normalize_text(str(title)),
                        section=section,
                        published_at=parse_datetime(str(published)) if published else None,
                        updated_at=parse_datetime(str(updated)) if updated else None,
                        summary=short_text(
                            str(_first(item, "summary", "description", "prologue") or "")
                        ),
                    )
                )
            except ValueError:
                continue
        if not articles:
            raise StructureChangedError("props.pageProps.list contained no parseable articles")
        return DiscoveryPage(articles=articles)

    def _parse_rss_path(self, content: bytes, url: str) -> DiscoveryPage:
        try:
            return parse_xml_feed(content, self.source)
        except StructureChangedError as xml_error:
            soup = BeautifulSoup(content, "html.parser")
            child_urls: list[str] = []
            for link in soup.select("a[href]"):
                try:
                    candidate = normalize_url(urljoin(url, str(link.get("href"))))
                except ValueError:
                    continue
                parts = urlsplit(candidate)
                if parts.hostname != "www.hani.co.kr" or candidate == normalize_url(url):
                    continue
                if parts.path.endswith(".xml") or parts.path.startswith("/rss/"):
                    child_urls.append(candidate)
            child_urls = list(dict.fromkeys(child_urls))
            if not child_urls:
                raise StructureChangedError(
                    "Hani RSS path was neither an XML feed nor an official RSS link list"
                ) from xml_error
            return DiscoveryPage(child_urls=child_urls)

    def extract_body(self, html_text: str, url: str) -> str:
        del url
        soup = BeautifulSoup(html_text, "html.parser")
        node = soup.select_one("article#renewal2023")
        if not node:
            raise StructureChangedError("article#renewal2023 not found")
        text = node.get_text("\n", strip=True)
        if not text:
            raise StructureChangedError("article#renewal2023 was empty")
        return text


def _first(item: dict[str, object], *keys: str) -> object | None:
    for key in keys:
        value = item.get(key)
        if value is not None and value != "":
            return value
    return None


def _section_name(value: object | None) -> str | None:
    if isinstance(value, dict):
        for key in ("name", "label", "code"):
            nested = value.get(key)
            if nested:
                return str(nested)
        return None
    return str(value) if value else None
