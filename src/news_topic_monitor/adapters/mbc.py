from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from html import unescape
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..models import ArticleDiscovery, DiscoveryPage
from ..utils import normalize_text, parse_datetime, short_text
from .base import SourceAdapter, SourceConfigurationError, StructureChangedError


class MbcAdapter(SourceAdapter):
    """Discover MBCNEWS videos through the official YouTube Data API.

    MBC's own news site currently blocks automated access in robots.txt. This
    adapter therefore uses only Google's documented API endpoints and keeps MBC
    article processing metadata-only. It combines keyword search with a local
    disability-term check of the channel's chronological uploads playlist so a
    search-index omission does not silently become an absence finding.
    """

    source = "mbc"
    media_group = "broadcast"
    api_origin = "https://youtube.googleapis.com"
    channel_id = "UCF4Wxdo3inmxP-Y59wXDsFw"
    allowed_discovery_hosts = frozenset({"youtube.googleapis.com"})
    allowed_article_hosts = frozenset({"www.youtube.com"})
    fetch_candidate_bodies = False

    search_queries = (
        "장애인|장애여성|장애아동|발달장애|중증장애|최중증",
        "휠체어|이동권|교통약자|접근성|편의시설|수어|점자",
        "탈시설|자립생활|지역사회통합|시설학대|거주시설",
        "정신장애|정신의료|강제입원|집단전원|재수용",
        "특수교육|교육권|장애인고용|권리중심공공일자리|활동지원",
    )
    upload_filter_terms = (
        "장애",
        "휠체어",
        "이동권",
        "교통약자",
        "접근성",
        "편의시설",
        "수어",
        "점자",
        "탈시설",
        "자립생활",
        "지역사회통합",
        "정신의료",
        "강제입원",
        "집단전원",
        "재수용",
        "특수교육",
        "교육권",
        "권리중심공공일자리",
        "활동지원",
    )

    def __init__(
        self,
        api_key: str | None = None,
        *,
        refresh_video_ids: list[str] | tuple[str, ...] = (),
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.refresh_video_ids = tuple(
            dict.fromkeys(
                video_id
                for video_id in refresh_video_ids
                if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id)
            )
        )
        self._window_start: datetime | None = None

    def initial_discovery_urls(self, start: datetime, end: datetime) -> list[str]:
        if not self.api_key:
            raise SourceConfigurationError("YOUTUBE_API_KEY is not configured")
        self._window_start = start.astimezone(UTC)
        common = {
            "part": "snippet",
            "channelId": self.channel_id,
            "type": "video",
            "order": "date",
            "maxResults": "50",
            "publishedAfter": _rfc3339(start),
            "publishedBefore": _rfc3339(end),
        }
        urls = [
            _api_url(
                "channels",
                {
                    "part": "contentDetails",
                    "id": self.channel_id,
                    "maxResults": "1",
                },
            )
        ]
        for query in self.search_queries:
            urls.append(_api_url("search", {**common, "q": query}))
        for offset in range(0, len(self.refresh_video_ids), 50):
            urls.append(
                _api_url(
                    "videos",
                    {
                        "part": "snippet",
                        "id": ",".join(self.refresh_video_ids[offset : offset + 50]),
                        "maxResults": "50",
                    },
                )
            )
        return urls

    def discovery_headers(self, url: str) -> dict[str, str]:
        parts = urlsplit(url)
        if parts.scheme == "https" and parts.hostname == "youtube.googleapis.com":
            return {"x-goog-api-key": self.api_key}
        return {}

    def parse_discovery(self, content: bytes, url: str) -> DiscoveryPage:
        payload = _parse_payload(content)
        endpoint = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
        if endpoint == "channels":
            return self._parse_channel(payload)
        if endpoint == "search":
            return self._parse_search(payload, url)
        if endpoint == "playlistItems":
            return self._parse_uploads(payload, url)
        if endpoint == "videos":
            return self._parse_refresh(payload, url)
        raise StructureChangedError(f"unsupported MBC YouTube endpoint: {endpoint}")

    def extract_body(self, html_text: str, url: str) -> str:
        raise StructureChangedError("MBC YouTube discovery is metadata-only")

    def _parse_channel(self, payload: dict[str, object]) -> DiscoveryPage:
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            raise StructureChangedError("MBCNEWS channel response contains no items")
        item = items[0]
        if not isinstance(item, dict) or item.get("id") != self.channel_id:
            raise StructureChangedError("MBCNEWS channel ID did not match the official channel")
        content_details = item.get("contentDetails")
        if not isinstance(content_details, dict):
            raise StructureChangedError("MBCNEWS channel contentDetails missing")
        related = content_details.get("relatedPlaylists")
        uploads = related.get("uploads") if isinstance(related, dict) else None
        if not isinstance(uploads, str) or not uploads:
            raise StructureChangedError("MBCNEWS uploads playlist ID missing")
        return DiscoveryPage(
            child_urls=[
                _api_url(
                    "playlistItems",
                    {
                        "part": "snippet,contentDetails",
                        "playlistId": uploads,
                        "maxResults": "50",
                    },
                )
            ]
        )

    def _parse_search(self, payload: dict[str, object], url: str) -> DiscoveryPage:
        articles: list[ArticleDiscovery] = []
        for item in _items(payload):
            identifier = item.get("id")
            video_id = identifier.get("videoId") if isinstance(identifier, dict) else None
            snippet = item.get("snippet")
            if not isinstance(video_id, str) or not isinstance(snippet, dict):
                continue
            article = self._article_from_snippet(video_id, snippet)
            if article is not None:
                articles.append(article)
        return DiscoveryPage(
            articles=articles,
            child_urls=_next_page_url(payload, url),
        )

    def _parse_uploads(self, payload: dict[str, object], url: str) -> DiscoveryPage:
        articles: list[ArticleDiscovery] = []
        page_dates: list[datetime] = []
        for item in _items(payload):
            snippet = item.get("snippet")
            content_details = item.get("contentDetails")
            if not isinstance(snippet, dict) or not isinstance(content_details, dict):
                continue
            video_id = content_details.get("videoId")
            if not isinstance(video_id, str):
                continue
            published_raw = content_details.get("videoPublishedAt") or snippet.get("publishedAt")
            if isinstance(published_raw, str):
                try:
                    published = parse_datetime(published_raw)
                except ValueError:
                    published = None
                if published is not None:
                    page_dates.append(published)
            title = _text(snippet.get("title"))
            description = _text(snippet.get("description"))
            if not _matches_any(f"{title} {description}", self.upload_filter_terms):
                continue
            article = self._article_from_snippet(video_id, snippet, published_raw=published_raw)
            if article is not None:
                articles.append(article)

        should_continue = True
        if self._window_start is not None and page_dates:
            should_continue = min(page_dates) >= self._window_start
        return DiscoveryPage(
            articles=articles,
            child_urls=_next_page_url(payload, url) if should_continue else [],
            stop_pagination=not should_continue,
        )

    def _parse_refresh(self, payload: dict[str, object], url: str) -> DiscoveryPage:
        query = dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))
        requested = {
            video_id
            for video_id in query.get("id", "").split(",")
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id)
        }
        if not requested:
            raise StructureChangedError("YouTube videos refresh request contains no valid IDs")
        articles: list[ArticleDiscovery] = []
        refreshed_ids: set[str] = set()
        for item in _items(payload):
            video_id = item.get("id")
            snippet = item.get("snippet")
            if not isinstance(video_id, str) or video_id not in requested:
                continue
            if not isinstance(snippet, dict):
                raise StructureChangedError("YouTube videos refresh item has no snippet")
            article = self._article_from_snippet(video_id, snippet, refresh_only=True)
            if article is not None:
                articles.append(article)
                refreshed_ids.add(video_id)
        return DiscoveryPage(
            articles=articles,
            removed_article_ids=sorted(requested - refreshed_ids),
        )

    def _article_from_snippet(
        self,
        video_id: str,
        snippet: dict[str, object],
        *,
        published_raw: object | None = None,
        refresh_only: bool = False,
    ) -> ArticleDiscovery | None:
        channel_id = snippet.get("videoOwnerChannelId") or snippet.get("channelId")
        if channel_id != self.channel_id:
            return None
        title = _text(snippet.get("title"))
        if not title:
            return None
        raw_date = published_raw or snippet.get("publishedAt")
        try:
            published_at = parse_datetime(raw_date) if isinstance(raw_date, str) else None
        except ValueError:
            return None
        return ArticleDiscovery(
            source=self.source,
            article_id=video_id,
            canonical_url=f"https://www.youtube.com/watch?v={video_id}",
            title=title,
            section="MBCNEWS YouTube",
            published_at=published_at,
            summary=short_text(_text(snippet.get("description")), 320),
            refresh_only=refresh_only,
        )


def _parse_payload(content: bytes) -> dict[str, object]:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StructureChangedError(f"invalid YouTube API JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise StructureChangedError("YouTube API response is not an object")
    if "error" in payload:
        raise StructureChangedError("YouTube API returned an error object")
    return payload


def _items(payload: dict[str, object]) -> list[dict[str, object]]:
    items = payload.get("items")
    if not isinstance(items, list):
        raise StructureChangedError("YouTube API items array missing")
    return [item for item in items if isinstance(item, dict)]


def _next_page_url(payload: dict[str, object], url: str) -> list[str]:
    token = payload.get("nextPageToken")
    if not isinstance(token, str) or not token:
        return []
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["pageToken"] = token
    return [urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))]


def _api_url(endpoint: str, query: dict[str, str]) -> str:
    return f"https://youtube.googleapis.com/youtube/v3/{endpoint}?{urlencode(query)}"


def _rfc3339(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(value: object) -> str:
    return normalize_text(unescape(value)) if isinstance(value, str) else ""


def _matches_any(value: str, terms: tuple[str, ...]) -> bool:
    normalized = normalize_text(value).casefold()
    return any(term.casefold() in normalized for term in terms)
