from __future__ import annotations

from datetime import UTC, datetime

import pytest

from news_topic_monitor.adapters.base import StructureChangedError, metadata_from_html
from news_topic_monitor.adapters.chosun import ChosunAdapter
from news_topic_monitor.adapters.donga import DongaAdapter
from news_topic_monitor.adapters.hani import HaniAdapter
from news_topic_monitor.adapters.joongang import JoongangAdapter


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
