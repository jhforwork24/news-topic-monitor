from .syndication import XmlSyndicationAdapter


class BeminorAdapter(XmlSyndicationAdapter):
    source = "beminor"
    media_group = "disability_press"
    allowed_discovery_hosts = frozenset({"www.beminor.com"})
    allowed_article_hosts = frozenset({"www.beminor.com"})
    discovery_urls = ("https://www.beminor.com/sitemap.xml",)
    body_selector = "#article-view-content-div"
