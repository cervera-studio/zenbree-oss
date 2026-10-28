from urllib.parse import quote


def switch_href(org_id: str, href: str) -> str:
    """What the panel, the stream and a push carry: ``href`` behind the web's /switch route, which makes ``org_id``
    the active organization first (the chat, campaign or reply lives only there). The row keeps the plain ``href``."""
    if href.startswith('/billing/switch'):
        return href
    return f'/switch?org={org_id}&next={quote(href, safe="")}'


def in_org(org_id: str, path: str) -> str:
    return switch_href(org_id, path) if org_id else path


def billing_href(org_id: str) -> str:
    """The billing page with ``org_id`` made the active organization (the web's /billing/switch route)."""
    return f'/billing/switch?org={org_id}'
