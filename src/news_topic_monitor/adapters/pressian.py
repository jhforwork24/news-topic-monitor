from .syndication import XmlSyndicationAdapter


class PressianAdapter(XmlSyndicationAdapter):
    source = "pressian"
    media_group = "general"
    supports_opinion_scan = True
    allowed_discovery_hosts = frozenset({"www.pressian.com"})
    allowed_article_hosts = frozenset({"www.pressian.com"})
    discovery_urls = ("https://www.pressian.com/api/v3/site/rss/news",)
    body_selector = ".article_body"
