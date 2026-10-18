from .syndication import XmlSyndicationAdapter


class PressianAdapter(XmlSyndicationAdapter):
    source = "pressian"
    media_group = "general"
    supports_opinion_scan = True
    allowed_discovery_hosts = frozenset({"www.pressian.com"})
    allowed_article_hosts = frozenset({"www.pressian.com"})
    # The RSS feed lists only the latest 25 items, so articles published while a collection
    # run failed (for example on a robots.txt timeout) fall out of it before the next run.
    # The official news sitemap lists about 2.5 days (about 120 items) with title and time.
    discovery_urls = (
        "https://www.pressian.com/api/v3/site/rss/news",
        "https://www.pressian.com/sitemap/news.xml",
    )
    body_selector = ".article_body"
