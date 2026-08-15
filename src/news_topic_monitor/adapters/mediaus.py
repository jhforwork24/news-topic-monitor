from .syndication import XmlSyndicationAdapter


class MediausAdapter(XmlSyndicationAdapter):
    source = "mediaus"
    media_group = "designated_column"
    supports_opinion_scan = True
    allowed_discovery_hosts = frozenset({"www.mediaus.co.kr"})
    allowed_article_hosts = frozenset({"www.mediaus.co.kr"})
    discovery_urls = ("https://www.mediaus.co.kr/sitemap.xml",)
    body_selector = "#article-view-content-div"
