from .syndication import XmlSyndicationAdapter


class LabortodayAdapter(XmlSyndicationAdapter):
    source = "labortoday"
    media_group = "labor_alternative"
    allowed_discovery_hosts = frozenset({"www.labortoday.co.kr"})
    allowed_article_hosts = frozenset({"www.labortoday.co.kr"})
    discovery_urls = ("https://www.labortoday.co.kr/sitemap.xml",)
    body_selector = "#article-view-content-div"
