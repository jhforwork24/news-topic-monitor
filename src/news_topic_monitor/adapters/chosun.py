from __future__ import annotations

import json
from datetime import datetime, timedelta
from urllib.parse import parse_qs, quote, urlsplit

from bs4 import BeautifulSoup

from ..models import ArticleDiscovery, DiscoveryPage
from ..utils import normalize_text, normalize_url, parse_datetime
from .base import (
    SourceAdapter,
    StructureChangedError,
    _article_id_from_url,
    decode_script_json_assignment,
    parse_xml_feed,
    text_from_content_elements,
)

# 기사피드 API의 한 쪽 크기와 분야별 최대 쪽수. 한 쪽은 filter를 쓰면 약 34KB이다
# (filter가 없으면 본문까지 담겨 약 8MB이므로 반드시 filter를 붙인다).
STORY_FEED_PAGE_SIZE = 100
STORY_FEED_MAX_PAGES = 12
STORY_FEED_FILTER = (
    "{content_elements{canonical_url,first_publish_date,display_date,"
    "last_updated_date,headlines{basic}},count}"
)
# 피드는 표시 시각(display_date) 내림차순이다. 수정으로 표시 시각이 갱신된 옛 기사가 앞쪽에
# 섞이므로 넘김 중단은 첫 게재 시각이 아니라 표시 시각으로 판단하고, 경계 부근의 시각 차이를
# 흡수하도록 수집 창 시작보다 이만큼 더 넘긴다.
STORY_FEED_FLOOR_MARGIN = timedelta(hours=1)
# 일본어판(/jp/)·영문판(/english/)은 브리핑 대상이 아니라 이 경로에서는 저장하지 않는다.
FOREIGN_EDITION_PREFIXES = ("/jp/", "/english/")


class ChosunAdapter(SourceAdapter):
    source = "chosun"
    media_group = "general"
    supports_opinion_scan = True
    allowed_discovery_hosts = frozenset({"www.chosun.com"})
    allowed_article_hosts = frozenset({"www.chosun.com"})
    # 기사피드의 다음 쪽을 자식 URL로 이어 붙인다(분야 7개 x 최대 11쪽).
    discovery_child_limit = 80
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

    # 기사피드 API(/pf/api)는 robots.txt가 막지 않는 공식 경로다(수집기 User-Agent 기준으로 확인).
    # 통합 RSS와 news-sitemap은 낮 시간대 1~2시간 분량, 분야 sitemap은 경제 약 8시간·스포츠 약
    # 2시간 분량(각 100건 상한)만 담아, 수집 회차가 비면 그 사이 기사를 놓친다. 기사피드는
    # 쪽을 넘겨 수집 창 시작까지 거슬러 올라갈 수 있다. 스포츠·연예 분야는 브리핑 대상이 아니고
    # 분야 sitemap으로 이미 수집하므로 피드에서는 뺀다.
    STORY_FEED_URL = "https://www.chosun.com/pf/api/v3/content/fetch/story-feed"
    STORY_FEED_SECTIONS = (
        "national",
        "politics",
        "economy",
        "international",
        "opinion",
        "culture-life",
        "medical",
    )

    def __init__(self) -> None:
        self._feed_floor: datetime | None = None

    def initial_discovery_urls(self, start: datetime, end: datetime) -> list[str]:
        del end
        self._feed_floor = start - STORY_FEED_FLOOR_MARGIN
        feed_urls = [self._story_feed_url(section, 0) for section in self.STORY_FEED_SECTIONS]
        return [self.RSS_URL, self.SITEMAP_URL, *self.CATEGORY_SITEMAP_URLS, *feed_urls]

    def discovery_headers(self, url: str) -> dict[str, str]:
        if url.startswith(self.STORY_FEED_URL):
            return {"Accept": "application/json"}
        return {}

    def parse_discovery(self, content: bytes, url: str) -> DiscoveryPage:
        if url.startswith(self.STORY_FEED_URL):
            return self._parse_story_feed(content, url)
        return parse_xml_feed(content, self.source)

    def _story_feed_url(self, section: str, offset: int) -> str:
        query = json.dumps(
            {
                "includeContentTypes": "story",
                "excludeContentTypes": "gallery, video",
                "includeSections": f"/{section}",
                "size": STORY_FEED_PAGE_SIZE,
                "offset": offset,
            },
            separators=(",", ":"),
        )
        return (
            f"{self.STORY_FEED_URL}?query={quote(query)}&_website=chosun"
            f"&filter={quote(STORY_FEED_FILTER)}"
        )

    def _parse_story_feed(self, content: bytes, url: str) -> DiscoveryPage:
        try:
            data = json.loads(content)
        except ValueError as exc:
            raise StructureChangedError(f"story-feed is not valid JSON: {exc}") from exc
        elements = data.get("content_elements") if isinstance(data, dict) else None
        if not isinstance(elements, list):
            raise StructureChangedError("story-feed has no content_elements list")
        articles: list[ArticleDiscovery] = []
        display_times: list[datetime] = []
        for element in elements:
            if not isinstance(element, dict):
                continue
            display = _feed_time(element.get("display_date") or element.get("last_updated_date"))
            if display is not None:
                display_times.append(display)
            path = element.get("canonical_url")
            headlines = element.get("headlines")
            title = headlines.get("basic") if isinstance(headlines, dict) else None
            if not isinstance(path, str) or not path.startswith("/") or not title:
                continue
            if path.startswith(FOREIGN_EDITION_PREFIXES):
                continue
            absolute = f"https://www.chosun.com{path}"
            try:
                articles.append(
                    ArticleDiscovery(
                        source=self.source,
                        article_id=_article_id_from_url(absolute),
                        canonical_url=normalize_url(absolute),
                        title=normalize_text(title),
                        published_at=_feed_time(element.get("first_publish_date")) or display,
                        updated_at=_feed_time(element.get("last_updated_date")),
                    )
                )
            except ValueError:
                continue
        child_urls: list[str] = []
        section, offset = _feed_request(url)
        reached_floor = bool(display_times) and (
            self._feed_floor is not None and min(display_times) < self._feed_floor
        )
        pages_done = offset // STORY_FEED_PAGE_SIZE + 1
        if (
            section is not None
            and len(elements) >= STORY_FEED_PAGE_SIZE
            and self._feed_floor is not None
            and display_times
            and not reached_floor
            and pages_done < STORY_FEED_MAX_PAGES
        ):
            child_urls.append(self._story_feed_url(section, offset + STORY_FEED_PAGE_SIZE))
        return DiscoveryPage(articles=articles, child_urls=child_urls)

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


def _feed_time(value: object) -> datetime | None:
    return parse_datetime(value) if isinstance(value, str) and value else None


def _feed_request(url: str) -> tuple[str | None, int]:
    """Recover the section and offset a story-feed URL asked for."""

    raw = parse_qs(urlsplit(url).query).get("query", [""])[0]
    try:
        query = json.loads(raw)
        section = str(query["includeSections"]).lstrip("/")
        return section, int(query.get("offset", 0))
    except (ValueError, KeyError, TypeError):
        return None, 0
