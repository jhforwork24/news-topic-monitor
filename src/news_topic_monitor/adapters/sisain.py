from .syndication import XmlSyndicationAdapter


class SisainAdapter(XmlSyndicationAdapter):
    source = "sisain"
    media_group = "general"
    supports_opinion_scan = True
    allowed_discovery_hosts = frozenset({"www.sisain.co.kr"})
    allowed_article_hosts = frozenset({"www.sisain.co.kr"})
    discovery_urls = ("https://www.sisain.co.kr/sitemap.xml",)
    body_selector = "#article-view-content-div"
