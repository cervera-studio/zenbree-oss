"""The Unipile account webhook's two writes: an accepted invitation and an account status (app/linkedin/api.py).

The route runs as ``System(LINKEDIN_WEBHOOK)``, and that scope only matches the account id to its connection, which
names the organization (one connection per Unipile account). The write and its notifications then run as
``ForOrg`` of that organization, so a wrong join can reach no other organization's targets or senders.
"""

from __future__ import annotations

import structlog

from app.core.db import get_conn
from app.core.scope import acting_for
from app.notifications import service as notifications

logger = structlog.get_logger()

_ACCOUNT_ORGANIZATION = """SELECT organization_id::text AS org_id FROM user_oauth_connections
WHERE provider = 'unipile' AND provider_account_id = %s"""


def _organization(account_id: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(_ACCOUNT_ORGANIZATION, (account_id,)).fetchone()
    return row['org_id'] if row else None


def accepted(payload: dict) -> dict:
    """An invitation was accepted: date it on the targets that invited this profile from campaigns of the
    projects the account serves.

    A clock, not a permission: the DM step still reads the profile's degree before it sends.
    """
    account_id = str(payload.get('account_id') or '')
    provider_id = str(payload.get('user_provider_id') or '')
    org_id = _organization(account_id) if account_id and provider_id else None
    if org_id is None:
        return {'handled': False, 'event': 'new_relation'}
    with acting_for(org_id, 'linkedin invitation accepted'), get_conn() as conn:
        dated = conn.execute(
            """UPDATE outreach_campaign_targets t SET linkedin_accepted_at = now()
                   FROM user_oauth_connections u, project_senders ps, outreach_campaigns c
                   WHERE u.provider = 'unipile' AND u.provider_account_id = %s AND ps.sender_id = u.id
                     AND c.project_id = ps.project_id
                     AND t.campaign_id = c.id AND t.owner_id IS NOT DISTINCT FROM c.owner_id
                     AND t.linkedin_provider_id = %s AND t.linkedin_accepted_at IS NULL
                   RETURNING c.id::text AS campaign_id, c.name AS campaign, c.owner_id::text AS owner_id,
                             t.first_name, t.last_name, t.company_name""",
            (account_id, provider_id),
        ).fetchall()
        notified = [
            notifications.notify(
                conn,
                t['owner_id'],
                'linkedin_accepted',
                href=f'/campaigns/{t["campaign_id"]}',
                data={
                    'name': f'{t["first_name"]} {t["last_name"]}'.strip() or t['company_name'],
                    'campaign': t['campaign'],
                    'channel': 'linkedin',
                },
                org_id=org_id,
            )
            for t in dated
            if t['owner_id']  # #333: the creator is gone, nobody to tell
        ]
    _deliver(notified)
    logger.info('linkedin.new_relation', targets=len(dated))
    return {'handled': bool(dated), 'event': 'new_relation', 'targets': len(dated)}


def account_status(account_id: str, healthy: bool, message: str) -> bool:
    """Unipile says the account works again or not: the sender follows, and whoever connected it hears of a break
    once. False when no live connection has this account id."""
    org_id = _organization(account_id) if account_id else None
    if org_id is None:
        return False
    fields = ', '.join(f'u.{f}' for f in notifications.CONNECTION_FIELDS)
    with acting_for(org_id, 'linkedin account status'), get_conn() as conn:
        moved = conn.execute(
            'UPDATE user_oauth_connections u SET status = %s, status_detail = %s FROM'
            " (SELECT id, status AS before FROM user_oauth_connections WHERE provider = 'unipile'"
            "  AND provider_account_id = %s AND status <> 'disconnected' FOR UPDATE) old WHERE u.id = old.id"
            f' RETURNING old.before, {fields}',
            ('connected' if healthy else 'error', None if healthy else message[:500], account_id),
        ).fetchall()
        notified = [
            notifications.notify_mailbox_error(conn, row)
            for row in moved
            if not healthy and row['before'] == 'connected'  # a repeated report tells nobody twice
        ]
    _deliver(notified)
    return bool(moved)


def _deliver(notified: list) -> None:
    """After the ``ForOrg`` block, never inside it: ``deliver`` acts as each recipient, and a scope cannot change
    while the block's connection is open."""
    for n in notified:
        notifications.deliver(n)
