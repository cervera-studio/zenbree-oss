"""The opening of a reply's text in the bell panel (#173), computed on read: ``notifications.data`` never holds it."""

from app.core.db import get_conn
from app.core.scope import ActorGone, acting_as
from app.outreach.inbound import strip_quoted_reply

REPLY_KINDS = frozenset({'reply_urgent', 'reply'})
QUOTE_CHARS = 140
QUOTE_SCAN_CHARS = 4000  # enough to find where the contact's own words end and our quoted copy starts


def quotes(conn, user_id: str, message_ids: list[str]) -> dict[str, str]:
    """The opening of each reply's text, by message id, for the panel only: ``notifications.data`` never holds
    it, so a message that was erased or deleted loses its quote. Another user's message gets none."""
    if not message_ids:
        return {}
    rows = conn.execute(
        """SELECT id::text AS id, left(body, %s) AS body
           FROM outreach_inbound_messages WHERE owner_id = %s AND id = ANY(%s::uuid[])""",
        (QUOTE_SCAN_CHARS, user_id, message_ids),
    ).fetchall()
    found = {r['id']: _clip(r['body'] or '') for r in rows}
    return {k: q for k, q in found.items() if q}


def _clip(body: str) -> str:
    text = ' '.join((strip_quoted_reply(body) or body).split())
    return text if len(text) <= QUOTE_CHARS else text[:QUOTE_CHARS].rstrip() + '…'


def with_quotes(conn, user_id: str, items: list[dict]) -> None:
    ids = [i['data']['message_id'] for i in items if i['kind'] in REPLY_KINDS and i['data'].get('message_id')]
    found = quotes(conn, user_id, ids)
    for i in items:
        q = found.get(i['data'].get('message_id') or '')
        if q:
            i['quote'] = q


def with_quotes_of_other_organizations(user_id: str, org_id: str, items: list[dict]) -> None:
    """The bell lists the user's notifications of every organization where they are a live member, but row-level
    security shows a reply only in its own organization: the quotes of the items outside ``org_id`` are read as the
    user in each item's organization. Call it with no connection open, since a scope cannot change while one is."""
    others: dict[str, list[dict]] = {}
    for i in items:
        if str(i['organization_id']) != org_id and i['kind'] in REPLY_KINDS and i['data'].get('message_id'):
            others.setdefault(str(i['organization_id']), []).append(i)
    for org, group in others.items():
        try:
            with acting_as(org, user_id), get_conn() as conn:
                with_quotes(conn, user_id, group)
        except ActorGone:
            continue  # left that organization after the list was read: its items show no quote
