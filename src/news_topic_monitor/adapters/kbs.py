from .syndication import XmlSyndicationAdapter


class KbsAdapter(XmlSyndicationAdapter):
    source = "kbs"
    media_group = "broadcast"
    allowed_discovery_hosts = frozenset({"news.kbs.co.kr"})
    allowed_article_hosts = frozenset({"news.kbs.co.kr"})
    discovery_urls = ("https://news.kbs.co.kr/sitemap/recentNewsList.xml",)
    body_selector = ".detail-body"
