from .syndication import XmlSyndicationAdapter


class SbsAdapter(XmlSyndicationAdapter):
    source = "sbs"
    media_group = "broadcast"
    allowed_discovery_hosts = frozenset({"news.sbs.co.kr"})
    allowed_article_hosts = frozenset({"news.sbs.co.kr"})
    discovery_urls = ("https://news.sbs.co.kr/news/sitemapRSS.do",)
    body_selector = "[itemprop='articleBody']"
