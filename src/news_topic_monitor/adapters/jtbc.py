from .syndication import XmlSyndicationAdapter


class JtbcAdapter(XmlSyndicationAdapter):
    source = "jtbc"
    media_group = "broadcast"
    allowed_discovery_hosts = frozenset({"news.jtbc.co.kr"})
    allowed_article_hosts = frozenset({"news.jtbc.co.kr"})
    discovery_urls = ("https://news.jtbc.co.kr/sitemaps/latest-articles",)
    body_selector = None
