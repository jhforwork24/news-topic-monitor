from .syndication import FailClosedAdapter


class NewschamAdapter(FailClosedAdapter):
    source = "newscham"
    media_group = "labor_alternative"
    allowed_discovery_hosts = frozenset({"www.newscham.net"})
    allowed_article_hosts = frozenset({"www.newscham.net"})
    discovery_url = "https://www.newscham.net/"
