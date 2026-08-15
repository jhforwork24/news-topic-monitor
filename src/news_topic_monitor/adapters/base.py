from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from datetime import datetime
from html import unescape
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from ..models import ArticleDiscovery, DiscoveryPage
from ..utils import normalize_text, normalize_url, parse_datetime, short_text


class StructureChangedError(ValueError):
    pass


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def child_text(element: ElementTree.Element, *names: str) -> str | None:
    wanted = {name.lower() for name in names}
    for child in list(element):
        if local_name(child.tag) in wanted and child.text:
            return child.text.strip()
    return None


def descendant_text(element: ElementTree.Element, *names: str) -> str | None:
    wanted = {name.lower() for name in names}
    for child in element.iter():
        if local_name(child.tag) in wanted and child.text:
            return child.text.strip()
    return None


def parse_xml_feed(content: bytes, source: str) -> DiscoveryPage:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise StructureChangedError(f"invalid XML: {exc}") from exc

    root_name = local_name(root.tag)
    articles: list[ArticleDiscovery] = []
    child_urls: list[str] = []

    if root_name in {"rss", "rdf"}:
        items = [element for element in root.iter() if local_name(element.tag) == "item"]
        if not items:
            raise StructureChangedError("RSS contains no item elements")
        for item in items:
            link = child_text(item, "link") or descendant_text(item, "link")
            title = child_text(item, "title")
            if not link or not title:
                continue
            published_raw = child_text(item, "pubdate", "published", "date")
            updated_raw = child_text(item, "updated", "lastmod")
            try:
                articles.append(
                    ArticleDiscovery(
                        source=source,
                        article_id=child_text(item, "guid"),
                        canonical_url=normalize_url(link),
                        title=normalize_text(title),
                        section=child_text(item, "category"),
                        published_at=parse_datetime(published_raw) if published_raw else None,
                        updated_at=parse_datetime(updated_raw) if updated_raw else None,
                        summary=short_text(child_text(item, "description", "summary")),
                    )
                )
            except ValueError:
                continue
        if not articles:
            raise StructureChangedError("RSS contains no valid article items")
    elif root_name == "urlset":
        urls = [element for element in list(root) if local_name(element.tag) == "url"]
        if not urls:
            raise StructureChangedError("sitemap contains no url elements")
        for element in urls:
            link = child_text(element, "loc")
            title = descendant_text(element, "title")
            published_raw = descendant_text(element, "publication_date", "pubdate")
            updated_raw = child_text(element, "lastmod")
            if not link:
                continue
            # Empty titles are rejected instead of inventing metadata. News sitemaps
            # are expected to provide news:title; a missing field is a structure signal.
            if not title:
                continue
            try:
                articles.append(
                    ArticleDiscovery(
                        source=source,
                        article_id=_article_id_from_url(link),
                        canonical_url=normalize_url(link),
                        title=normalize_text(title),
                        section=descendant_text(element, "keywords"),
                        published_at=parse_datetime(published_raw) if published_raw else None,
                        updated_at=parse_datetime(updated_raw) if updated_raw else None,
                    )
                )
            except ValueError:
                continue
        if not articles:
            raise StructureChangedError("sitemap contains no valid news article URLs")
    elif root_name == "sitemapindex":
        for element in list(root):
            if local_name(element.tag) == "sitemap":
                link = child_text(element, "loc")
                if link:
                    child_urls.append(normalize_url(link))
        if not child_urls:
            raise StructureChangedError("sitemap index contains no child sitemap URLs")
    else:
        raise StructureChangedError(f"unsupported XML root: {root_name}")
    return DiscoveryPage(articles=articles, child_urls=child_urls)


def extract_json_ld(soup: BeautifulSoup) -> dict[str, object]:
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text()
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        candidates = value if isinstance(value, list) else [value]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("@type") in {
                "Article",
                "NewsArticle",
                "ReportageNewsArticle",
            }:
                return candidate
            if isinstance(candidate, dict) and isinstance(candidate.get("@graph"), list):
                for nested in candidate["@graph"]:
                    if isinstance(nested, dict) and nested.get("@type") in {
                        "Article",
                        "NewsArticle",
                        "ReportageNewsArticle",
                    }:
                        return nested
    return {}


def metadata_from_html(html_text: str, page_url: str) -> dict[str, str | None]:
    soup = BeautifulSoup(html_text, "html.parser")
    json_ld = extract_json_ld(soup)
    canonical = soup.select_one('link[rel="canonical"]')
    title_meta = soup.select_one('meta[property="og:title"]')
    description = soup.select_one('meta[property="og:description"], meta[name="description"]')
    section = soup.select_one('meta[property="article:section"]')
    published = json_ld.get("datePublished") or _meta_content(soup, "article:published_time")
    modified = json_ld.get("dateModified") or _meta_content(soup, "article:modified_time")
    return {
        "canonical_url": normalize_url(
            urljoin(page_url, canonical.get("href"))
            if canonical and canonical.get("href")
            else page_url
        ),
        "title": normalize_text(
            str(json_ld.get("headline") or (title_meta.get("content") if title_meta else ""))
        )
        or None,
        "summary": short_text(
            str(json_ld.get("description") or (description.get("content") if description else ""))
        ),
        "section": normalize_text(section.get("content"))
        if section and section.get("content")
        else None,
        "published_at": str(published) if published else None,
        "updated_at": str(modified) if modified else None,
    }


def _meta_content(soup: BeautifulSoup, property_name: str) -> str | None:
    node = soup.select_one(f'meta[property="{property_name}"]')
    return str(node.get("content")) if node and node.get("content") else None


def _article_id_from_url(url: str) -> str | None:
    matches = re.findall(r"\d{5,}", urlsplit(url).path)
    return matches[-1] if matches else None


class SourceAdapter(ABC):
    source: str
    allowed_discovery_hosts: frozenset[str]
    allowed_article_hosts: frozenset[str]

    @abstractmethod
    def initial_discovery_urls(self, start: datetime, end: datetime) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def parse_discovery(self, content: bytes, url: str) -> DiscoveryPage:
        raise NotImplementedError

    @abstractmethod
    def extract_body(self, html_text: str, url: str) -> str:
        raise NotImplementedError

    def validate_child_url(self, url: str) -> bool:
        return urlsplit(url).hostname in self.allowed_discovery_hosts

    def validate_article_url(self, url: str) -> bool:
        return urlsplit(url).hostname in self.allowed_article_hosts


def decode_script_json_assignment(script: str, marker: str) -> dict[str, object]:
    index = script.find(marker)
    if index < 0:
        raise StructureChangedError(f"script marker not found: {marker}")
    tail = script[index + len(marker) :].lstrip(" =:\n\t")
    try:
        value, _ = json.JSONDecoder().raw_decode(tail)
    except json.JSONDecodeError as exc:
        raise StructureChangedError(f"invalid JSON after {marker}: {exc}") from exc
    if not isinstance(value, dict):
        raise StructureChangedError(f"{marker} value is not an object")
    return value


def text_from_content_elements(elements: object) -> str:
    paragraphs: list[str] = []
    if isinstance(elements, list):
        for element in elements:
            if not isinstance(element, dict):
                continue
            element_type = str(element.get("type", ""))
            if element_type in {"text", "header", "raw_html", "blockquote"}:
                value = element.get("content") or element.get("text")
                if value:
                    paragraphs.append(normalize_text(unescape(str(value))))
            nested = element.get("content_elements")
            if nested:
                nested_text = text_from_content_elements(nested)
                if nested_text:
                    paragraphs.append(nested_text)
    return "\n".join(value for value in paragraphs if value)
