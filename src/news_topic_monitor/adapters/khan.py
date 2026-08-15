from .syndication import XmlSyndicationAdapter


class KhanAdapter(XmlSyndicationAdapter):
    source = "khan"
    media_group = "general"
    supports_opinion_scan = True
    allowed_discovery_hosts = frozenset({"www.khan.co.kr"})
    allowed_article_hosts = frozenset({"www.khan.co.kr"})
    discovery_urls = ("https://www.khan.co.kr/sitemap/latest-articles.xml",)
    body_selector = "#articleBody"
