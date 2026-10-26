from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

from news_topic_monitor.adapters.base import StructureChangedError, metadata_from_html
from news_topic_monitor.adapters.chosun import ChosunAdapter
from news_topic_monitor.adapters.donga import DongaAdapter
from news_topic_monitor.adapters.hani import HaniAdapter
from news_topic_monitor.adapters.joongang import JoongangAdapter

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("adapter", "fixture_name", "expected_host"),
    [
        (ChosunAdapter(), "chosun_rss.xml", "chosun.com"),
        (JoongangAdapter(), "joongang_sitemap.xml", "joongang.co.kr"),
        (DongaAdapter(), "donga_rss.xml", "donga.com"),
        (HaniAdapter(), "hani_list.html", "hani.co.kr"),
    ],
)
def test_source_discovery_parsers(adapter, fixture_name, expected_host, fixture_dir) -> None:
    content = (fixture_dir / fixture_name).read_bytes()
    page = adapter.parse_discovery(content, "https://www.hani.co.kr/arti?page=1")
    assert len(page.articles) == 1
    article = page.articles[0]
    assert expected_host in article.canonical_url
    assert article.title
    assert article.section
    assert article.published_at is not None
    assert article.published_at.tzinfo == UTC


def test_empty_rss_is_structure_change() -> None:
    with pytest.raises(StructureChangedError):
        ChosunAdapter().parse_discovery(b"<rss><channel/></rss>", "https://example.test/feed")


def test_invalid_article_is_skipped_while_valid_item_survives() -> None:
    xml = b"""<rss><channel>
      <item><title>bad</title><link>not-a-url</link><pubDate>not-a-date</pubDate></item>
      <item><title>valid</title><link>https://www.chosun.com/national/example</link>
      <pubDate>Sat, 15 Aug 2026 00:30:00 +0900</pubDate></item>
    </channel></rss>"""
    page = ChosunAdapter().parse_discovery(xml, "https://www.chosun.com/feed")
    assert [article.title for article in page.articles] == ["valid"]


def test_missing_hani_next_data_is_structure_change() -> None:
    with pytest.raises(StructureChangedError, match="__NEXT_DATA__"):
        HaniAdapter().parse_discovery(b"<html></html>", "https://www.hani.co.kr/arti?page=1")


def test_hani_auxiliary_rss(fixture_dir) -> None:
    page = HaniAdapter().parse_discovery(
        (fixture_dir / "hani_rss.xml").read_bytes(), "https://www.hani.co.kr/rss/"
    )
    assert len(page.articles) == 1
    assert page.articles[0].source == "hani"


def test_body_selectors_and_metadata(fixture_dir) -> None:
    html = (fixture_dir / "article_bodies.html").read_text(encoding="utf-8")
    assert "이동권" in ChosunAdapter().extract_body(html, "https://www.chosun.com/x")
    assert "활동지원" in JoongangAdapter().extract_body(html, "https://www.joongang.co.kr/x")
    assert "노동권" in DongaAdapter().extract_body(html, "https://www.donga.com/x")
    assert "탈시설" in HaniAdapter().extract_body(html, "https://www.hani.co.kr/x")
    metadata = metadata_from_html(html, "https://example.com/article/1")
    assert metadata["title"] == "장애인 활동지원 기사"
    assert metadata["canonical_url"] == "https://example.com/article/1"
    assert metadata["byline"] == "홍길동 기자"


def test_model_rejects_empty_title() -> None:
    from news_topic_monitor.models import ArticleDiscovery

    with pytest.raises(ValueError):
        ArticleDiscovery(
            source="test",
            canonical_url="https://example.com/1",
            title=" ",
            published_at=datetime(2026, 8, 15, tzinfo=UTC),
        )


def test_chosun_discovery_includes_category_news_sitemaps_and_story_feed() -> None:
    adapter = ChosunAdapter()
    urls = adapter.initial_discovery_urls(
        datetime(2026, 10, 19, tzinfo=UTC), datetime(2026, 10, 26, tzinfo=UTC)
    )
    assert urls[:2] == [ChosunAdapter.RSS_URL, ChosunAdapter.SITEMAP_URL]
    assert len(adapter.CATEGORY_SITEMAP_URLS) == 9
    sitemaps, feeds = urls[2:11], urls[11:]
    assert all(
        u.startswith("https://www.chosun.com/arc/outboundfeeds/news-sitemap/category/")
        for u in sitemaps
    )
    # 스포츠·연예는 브리핑 대상이 아니고 분야 sitemap으로 수집하므로 기사피드에서 뺀다.
    assert len(feeds) == len(adapter.STORY_FEED_SECTIONS) == 7
    assert all(u.startswith(ChosunAdapter.STORY_FEED_URL) for u in feeds)
    # filter가 없으면 한 쪽이 약 8MB(본문 포함)이므로 반드시 붙어 있어야 한다.
    assert all("filter=" in u and "content_elements" in u for u in feeds)
    assert all(urlparse(u).hostname in adapter.allowed_discovery_hosts for u in urls)
    assert adapter.discovery_headers(feeds[0]) == {"Accept": "application/json"}
    assert adapter.discovery_headers(urls[0]) == {}


def test_chosun_category_sitemap_fixture_parses_titles_and_urls() -> None:
    page = ChosunAdapter().parse_discovery(
        (FIXTURES / "chosun_category_sitemap.xml").read_bytes(),
        ChosunAdapter.CATEGORY_SITEMAP_URLS[0],
    )
    assert "박순관 대표 징역 4년 확정" in page.articles[1].title
    assert all(a.canonical_url.startswith("https://www.chosun.com/") for a in page.articles)


def _feed_page(count: int, *, newest: datetime, step_minutes: int = 5) -> bytes:
    import json
    from datetime import timedelta

    elements = []
    for index in range(count):
        shown = (
            (newest - timedelta(minutes=step_minutes * index)).isoformat().replace("+00:00", "Z")
        )
        elements.append(
            {
                "canonical_url": f"/national/regional/2026/10/26/ID{index:024d}/",
                "first_publish_date": shown,
                "display_date": shown,
                "last_updated_date": shown,
                "headlines": {"basic": f"합성 기사 {index}"},
            }
        )
    return json.dumps({"content_elements": elements, "count": count}).encode()


def test_chosun_story_feed_fixture_keeps_korean_stories_with_first_publish_time(
    fixture_dir,
) -> None:
    adapter = ChosunAdapter()
    feed_url = adapter.initial_discovery_urls(
        datetime(2026, 10, 25, tzinfo=UTC), datetime(2026, 10, 26, tzinfo=UTC)
    )[-1]
    page = adapter.parse_discovery((fixture_dir / "chosun_story_feed.json").read_bytes(), feed_url)
    titles = [article.title for article in page.articles]
    # 일본어판과 제목 없는 항목은 저장하지 않는다.
    assert titles == ["합성 지역 기사 제목", "합성 산업 기사 제목", "합성 수정된 옛 기사 제목"]
    assert all(a.canonical_url.startswith("https://www.chosun.com/") for a in page.articles)
    assert all(a.published_at is not None and a.published_at.tzinfo == UTC for a in page.articles)
    # 표시 시각이 갱신된 옛 기사는 첫 게재 시각을 유지한다(수집 창 밖으로 걸러지게 한다).
    assert page.articles[-1].published_at == datetime(2026, 9, 1, tzinfo=UTC)
    assert page.child_urls == []


def test_chosun_story_feed_pages_back_to_the_collection_window_floor() -> None:
    adapter = ChosunAdapter()
    start = datetime(2026, 10, 26, 3, tzinfo=UTC)
    first = adapter.initial_discovery_urls(start, datetime(2026, 10, 26, 9, tzinfo=UTC))[-1]
    # 100건이 5분 간격: 가장 오래된 항목이 창 시작 - 1시간보다 새로우면 다음 쪽을 이어 붙인다.
    page = adapter.parse_discovery(
        _feed_page(100, newest=datetime(2026, 10, 26, 12, tzinfo=UTC)), first
    )
    assert len(page.child_urls) == 1
    assert '"offset":100' in unquote(page.child_urls[0])
    assert "/medical" in unquote(page.child_urls[0])
    # 가장 오래된 항목이 창 경계보다 오래되면 거기서 멈춘다.
    done = adapter.parse_discovery(
        _feed_page(100, newest=datetime(2026, 10, 26, 6, tzinfo=UTC)), first
    )
    assert done.child_urls == []
    # 한 쪽이 다 차지 않았으면 끝이다.
    short = adapter.parse_discovery(
        _feed_page(40, newest=datetime(2026, 10, 26, 12, tzinfo=UTC)), first
    )
    assert short.child_urls == []


def test_chosun_story_feed_stops_at_the_maximum_page_count() -> None:
    from news_topic_monitor.adapters.chosun import STORY_FEED_MAX_PAGES, STORY_FEED_PAGE_SIZE

    adapter = ChosunAdapter()
    start = datetime(2026, 10, 20, tzinfo=UTC)
    first = adapter.initial_discovery_urls(start, datetime(2026, 10, 26, tzinfo=UTC))[-1]
    last_offset = (STORY_FEED_MAX_PAGES - 1) * STORY_FEED_PAGE_SIZE
    last_url = first.replace("%22offset%22%3A0", f"%22offset%22%3A{last_offset}")
    assert last_url != first
    page = adapter.parse_discovery(
        _feed_page(100, newest=datetime(2026, 10, 26, tzinfo=UTC)), last_url
    )
    assert page.child_urls == []


@pytest.mark.parametrize("content", [b"not json", b"{}", b'{"content_elements": "x"}'])
def test_chosun_story_feed_structure_change_is_reported(content) -> None:
    adapter = ChosunAdapter()
    feed_url = adapter.initial_discovery_urls(
        datetime(2026, 10, 25, tzinfo=UTC), datetime(2026, 10, 26, tzinfo=UTC)
    )[-1]
    with pytest.raises(StructureChangedError):
        adapter.parse_discovery(content, feed_url)
