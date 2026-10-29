"""Write-back to HubSpot (E13.2, #121): may this user's connection write, and did they turn it on?

Two facts, kept apart. The grant either carries the write scopes or not: the app asks for them as optional
scopes, and HubSpot's consent screen lets the user tick each one, so a grant may have the contacts scope and
not the companies one. The switch is the user's own choice, stored on the connection and off until they turn
it on: agreeing to a consent screen is not agreeing to what Zenbree will write. Everything that writes to
HubSpot reads :func:`state` and writes only when both hold.
"""

from typing import Literal

from app.core.db import get_conn
from app.integrations.providers import HUBSPOT_COMPANIES_WRITE, HUBSPOT_CONTACTS_WRITE

OFF = {'can_write': False, 'can_write_companies': False, 'write_back': False}


def state(user_id: str) -> dict:
    """``can_write``: the connected grant may write contacts, which is all write-back needs.
    ``can_write_companies``: it may write companies too; without it contacts get no company.
    ``write_back``: it can write and the switch is on."""
    with get_conn() as conn:
        row = conn.execute(
            'SELECT %s = ANY(granted_scopes) AS contacts, %s = ANY(granted_scopes) AS companies, crm_write_back'
            " FROM user_oauth_connections WHERE user_id = %s AND provider = 'hubspot' AND status = 'connected'",
            (HUBSPOT_CONTACTS_WRITE, HUBSPOT_COMPANIES_WRITE, user_id),
        ).fetchone()
    if not row:
        return dict(OFF)
    return {
        'can_write': row['contacts'],
        'can_write_companies': row['contacts'] and row['companies'],
        'write_back': row['contacts'] and row['crm_write_back'],
    }


def set_enabled(user_id: str, enabled: bool) -> Literal['ok', 'not_connected', 'no_write_scope']:
    """Store the switch. Turning it on needs a connected grant with the contacts write scope; turning it off never
    fails on a connected one, so a user can always stop the writes."""
    with get_conn() as conn:
        row = conn.execute(
            # ``since`` moves only when the switch goes from off to on: people written to before stay unwritten (#527).
            'UPDATE user_oauth_connections SET crm_write_back_since = CASE WHEN NOT %s THEN NULL'
            ' WHEN crm_write_back THEN crm_write_back_since ELSE now() END, crm_write_back = %s'
            " WHERE user_id = %s AND provider = 'hubspot' AND status = 'connected'"
            ' AND (NOT %s OR %s = ANY(granted_scopes)) RETURNING id',
            (enabled, enabled, user_id, enabled, HUBSPOT_CONTACTS_WRITE),
        ).fetchone()
        if row:
            return 'ok'
        connected = conn.execute(
            "SELECT 1 FROM user_oauth_connections WHERE user_id = %s AND provider = 'hubspot' AND status = 'connected'",
            (user_id,),
        ).fetchone()
    return 'no_write_scope' if connected else 'not_connected'
