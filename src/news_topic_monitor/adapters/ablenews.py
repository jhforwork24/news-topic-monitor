from .syndication import XmlSyndicationAdapter


class AblenewsAdapter(XmlSyndicationAdapter):
    source = "ablenews"
    media_group = "disability_press"
    allowed_discovery_hosts = frozenset({"www.ablenews.co.kr"})
    allowed_article_hosts = frozenset({"www.ablenews.co.kr"})
    discovery_urls = ("https://www.ablenews.co.kr/sitemap.xml",)
    body_selector = "#article-view-content-div"
