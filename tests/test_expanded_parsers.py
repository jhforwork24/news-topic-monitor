from __future__ import annotations

from datetime import UTC, datetime

import pytest

from news_topic_monitor.adapters.ablenews import AblenewsAdapter
from news_topic_monitor.adapters.base import StructureChangedError
from news_topic_monitor.adapters.beminor import BeminorAdapter
from news_topic_monitor.adapters.khan import KhanAdapter
from news_topic_monitor.adapters.labortoday import LabortodayAdapter
from news_topic_monitor.adapters.mediaus import MediausAdapter
from news_topic_monitor.adapters.newscham import NewschamAdapter
from news_topic_monitor.adapters.ohmynews import OhmynewsAdapter
from news_topic_monitor.adapters.pressian import PressianAdapter
from news_topic_monitor.adapters.sisain import SisainAdapter
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
            MediausAdapter(),
            "https://www.mediaus.co.kr/news/articleView.html?idxno=317842",
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
            SisainAdapter(),
            "https://www.sisain.co.kr/news/articleView.html?idxno=52341",
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
        assert page.articles[0].byline == "김기자 기자"
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
        (MediausAdapter(), "article-view-content-div"),
        (BeminorAdapter(), "article-view-content-div"),
        (AblenewsAdapter(), "article-view-content-div"),
        (SisainAdapter(), "article-view-content-div"),
        (TheindigoAdapter(), "td-post-content"),
    ],
)
def test_verified_expanded_body_selectors(adapter, selector) -> None:
    html = f"<html><div id='{selector}' class='{selector}' {selector}>짧은 판별용 문장</div></html>"
    assert "짧은 판별용" in adapter.extract_body(html, "https://example.test/article")


def test_newscham_article_list_parser(fixture_dir) -> None:
    adapter = NewschamAdapter()
    urls = adapter.initial_discovery_urls(
        datetime(2026, 9, 22, tzinfo=UTC), datetime(2026, 9, 23, tzinfo=UTC)
    )
    assert urls[0] == "https://www.newscham.net/articles/?page=1"
    assert all(url.startswith(adapter.date_ordered_list_prefix) for url in urls)
    page = adapter.parse_discovery((fixture_dir / "newscham_list.html").read_bytes(), urls[0])
    # The sidebar "recent articles" widget and the 기사수정 edit link are not list items.
    assert [article.article_id for article in page.articles] == ["900002", "900001"]
    first, second = page.articles
    assert first.canonical_url == "https://www.newscham.net/articles/900002"
    assert first.title == "시험용 노동 기사 제목"
    assert first.section == "노동"
    assert first.byline == "시험 기자"
    assert first.summary == "시험용 요약 문장이다."
    # Displayed times are KST; a single-digit hour is still parsed.
    assert first.published_at == datetime(2026, 9, 23, 8, 11, tzinfo=UTC)
    assert second.published_at == datetime(2026, 9, 22, 0, 51, tzinfo=UTC)
    assert all(adapter.validate_article_url(article.canonical_url) for article in page.articles)


def test_newscham_list_without_items_is_structure_change() -> None:
    with pytest.raises(StructureChangedError, match=r"article\.figure"):
        NewschamAdapter().parse_discovery(
            b"<html><section class='mainContents'></section></html>",
            "https://www.newscham.net/articles/?page=1",
        )


def test_newscham_body_selector() -> None:
    html = (
        "<html><article id='news-article-post'><header id='news-article-header'>"
        "<hgroup class='news-article-subject'><h1>제목</h1></hgroup></header>"
        "<div id='news-article-content' class='content zoom'><p>짧은 판별용 문장</p></div>"
        "</article></html>"
    )
    adapter = NewschamAdapter()
    assert adapter.extract_body(html, "https://www.newscham.net/articles/900001") == (
        "짧은 판별용 문장"
    )
    with pytest.raises(StructureChangedError):
        adapter.extract_body("<html></html>", "https://www.newscham.net/articles/900001")
