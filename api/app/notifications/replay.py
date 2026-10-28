"""A replayed ``notification`` frame is re-read through the panel's query (#975): the Redis frame is a stale copy
made when the user may have been a member of an organization they have left, with a quote the message may no longer
back. Only the id is trusted."""

import json

from app.core.db import get_conn
from app.notifications.hrefs import switch_href
from app.notifications.quotes import with_quotes, with_quotes_of_other_organizations
from app.notifications.service import VISIBLE_SQL, browser_on, prefs, unread_count


def fresh_frame_data(user_id: str, org_id: str, raw: str) -> str | None:
    """The frame's JSON with its item and unread count as the panel shows them now; None when the user cannot see
    the item any more (left its organization, row gone) or the frame has no item id."""
    data = json.loads(raw)
    item_id = (data.get('item') or {}).get('id')
    if not item_id:
        return None
    with get_conn() as conn:
        row = conn.execute(
            f"""SELECT id::text AS id, organization_id, kind, href, data, read_at IS NOT NULL AS read, created_at
               FROM notifications n WHERE n.id::text = %s AND n.user_id = %s AND {VISIBLE_SQL}""",
            (item_id, user_id),
        ).fetchone()
        if row is None:
            return None
        if str(row['organization_id']) == org_id:
            with_quotes(conn, user_id, [row])
        row['browser'] = browser_on(prefs(conn, user_id), row['kind'])
        unread = unread_count(conn, user_id)
    with_quotes_of_other_organizations(user_id, org_id, [row])
    row['href'] = switch_href(str(row.pop('organization_id')), row['href'])
    return json.dumps({'item': row, 'unread': unread}, default=str)
