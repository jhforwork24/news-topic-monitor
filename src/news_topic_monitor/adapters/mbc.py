from .syndication import FailClosedAdapter


class MbcAdapter(FailClosedAdapter):
    source = "mbc"
    media_group = "broadcast"
    allowed_discovery_hosts = frozenset({"imnews.imbc.com"})
    allowed_article_hosts = frozenset({"imnews.imbc.com"})
    discovery_url = "https://imnews.imbc.com/"
