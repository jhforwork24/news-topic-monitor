from .syndication import XmlSyndicationAdapter


class OhmynewsAdapter(XmlSyndicationAdapter):
    source = "ohmynews"
    media_group = "general"
    supports_opinion_scan = True
    allowed_discovery_hosts = frozenset({"www.ohmynews.com"})
    allowed_article_hosts = frozenset({"www.ohmynews.com"})
    discovery_urls = ("https://www.ohmynews.com/NWS_Web/View/latestnews.aspx",)
    body_selector = "[itemprop='articleBody']"
