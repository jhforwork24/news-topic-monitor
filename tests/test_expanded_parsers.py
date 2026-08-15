from __future__ import annotations

from datetime import UTC, datetime

import pytest

from news_topic_monitor.adapters.ablenews import AblenewsAdapter
from news_topic_monitor.adapters.base import StructureChangedError
from news_topic_monitor.adapters.beminor import BeminorAdapter
from news_topic_monitor.adapters.jtbc import JtbcAdapter
from news_topic_monitor.adapters.kbs import KbsAdapter
from news_topic_monitor.adapters.khan import KhanAdapter
from news_topic_monitor.adapters.labortoday import LabortodayAdapter
from news_topic_monitor.adapters.mbc import MbcAdapter
from news_topic_monitor.adapters.newscham import NewschamAdapter
from news_topic_monitor.adapters.ohmynews import OhmynewsAdapter
from news_topic_monitor.adapters.pressian import PressianAdapter
from news_topic_monitor.adapters.sbs import SbsAdapter
from news_topic_monitor.adapters.theindigo import TheindigoAdapter


@pytest.mark.parametrize(
    ("adapter", "url", "fixture_name"),
    [
        (
            KhanAdapter(),
            "https://www.khan.co.kr/article/202608151234001",
            "generic_news_sitemap.xml",
        ),
        (
            OhmynewsAdapter(),
            "https://www.ohmynews.com/NWS_Web/View/at_pg.aspx?CNTN_CD=A0000000001",
            "generic_news_sitemap.xml",
        ),
        (
            PressianAdapter(),
            "https://www.pressian.com/pages/articles/2026081512340000001",
            "generic_rss.xml",
        ),
        (
            LabortodayAdapter(),
            "https://www.labortoday.co.kr/news/articleView.html?idxno=236263",
            "generic_news_sitemap.xml",
        ),
        (
            BeminorAdapter(),
            "https://www.beminor.com/news/articleView.html?idxno=30281",
            "generic_news_sitemap.xml",
        ),
        (
            AblenewsAdapter(),
            "https://www.ablenews.co.kr/news/articleView.html?idxno=232606",
            "generic_news_sitemap.xml",
        ),
        (
            KbsAdapter(),
            "https://news.kbs.co.kr/news/view.do?ncd=8638004",
            "generic_news_sitemap.xml",
        ),
        (
            SbsAdapter(),
            "https://news.sbs.co.kr/news/endPage.do?news_id=N1008706921",
            "generic_rss.xml",
        ),
        (
            JtbcAdapter(),
            "https://news.jtbc.co.kr/article/NB12313535",
            "generic_news_sitemap.xml",
        ),
    ],
)
def test_expanded_xml_parsers(adapter, url, fixture_name, fixture_dir) -> None:
    text = (fixture_dir / fixture_name).read_text(encoding="utf-8")
    text = text.replace("SOURCE_URL", url).replace(
        "SOURCE_TITLE", "&amp;lt;![CDATA[장애인 접근권 기사]]&amp;gt;"
    )
    page = adapter.parse_discovery(
        text.encode(),
        adapter.initial_discovery_urls(
            datetime(2026, 8, 14, tzinfo=UTC), datetime(2026, 8, 16, tzinfo=UTC)
        )[0],
    )
    assert len(page.articles) == 1
    assert page.articles[0].source == adapter.source
    if fixture_name.endswith("sitemap.xml"):
        assert page.articles[0].title == "장애인 접근권 기사"
    else:
        assert page.articles[0].title == "장애인 이동권 보도"
    assert page.articles[0].published_at is not None


def test_pressian_double_escaped_tracking_parameter_is_removed(fixture_dir) -> None:
    text = (
        (fixture_dir / "generic_rss.xml")
        .read_text(encoding="utf-8")
        .replace(
            "SOURCE_URL",
            "https://www.pressian.com/pages/articles/2026081512340000001&amp;amp;ref=rss",
        )
    )
    article = (
        PressianAdapter().parse_discovery(text.encode(), "https://www.pressian.com/rss").articles[0]
    )
    assert article.canonical_url == "https://www.pressian.com/pages/articles/2026081512340000001"


def test_theindigo_wordpress_metadata_parser_and_window(fixture_dir) -> None:
    adapter = TheindigoAdapter()
    urls = adapter.initial_discovery_urls(
        datetime(2026, 8, 14, tzinfo=UTC), datetime(2026, 8, 16, tzinfo=UTC)
    )
    assert "_fields=" in urls[0]
    assert "content" not in urls[0]
    page = adapter.parse_discovery((fixture_dir / "theindigo_posts.json").read_bytes(), urls[0])
    assert page.articles[0].canonical_url.endswith("/archives/69368")
    assert page.articles[0].summary == "공개 API가 제공한 짧은 요약"


def test_theindigo_empty_window_is_successful_empty_discovery() -> None:
    page = TheindigoAdapter().parse_discovery(b"[]", "https://theindigo.co.kr/wp-json/wp/v2/posts")
    assert page.articles == []


@pytest.mark.parametrize(
    ("adapter", "selector"),
    [
        (KhanAdapter(), "articleBody"),
        (OhmynewsAdapter(), "itemprop='articleBody'"),
        (PressianAdapter(), "article_body"),
        (LabortodayAdapter(), "article-view-content-div"),
        (BeminorAdapter(), "article-view-content-div"),
        (AblenewsAdapter(), "article-view-content-div"),
        (TheindigoAdapter(), "td-post-content"),
        (KbsAdapter(), "detail-body"),
        (SbsAdapter(), "itemprop='articleBody'"),
    ],
)
def test_verified_expanded_body_selectors(adapter, selector) -> None:
    html = f"<html><div id='{selector}' class='{selector}' {selector}>짧은 판별용 문장</div></html>"
    assert "짧은 판별용" in adapter.extract_body(html, "https://example.test/article")


def test_jtbc_does_not_guess_client_rendered_body_selector() -> None:
    with pytest.raises(StructureChangedError, match="selector is unavailable"):
        JtbcAdapter().extract_body("<html></html>", "https://news.jtbc.co.kr/article/NB1")


@pytest.mark.parametrize("adapter", [MbcAdapter(), NewschamAdapter()])
def test_fail_closed_sources_have_no_parser(adapter) -> None:
    with pytest.raises(StructureChangedError, match="fail-closed"):
        adapter.parse_discovery(b"<html></html>", adapter.discovery_url)
