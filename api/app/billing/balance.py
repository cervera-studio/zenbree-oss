"""Balance changes (#243): every committed change to ``user_credits.credits_remaining`` queues one
``on_balance_changed`` task for that organization's wallet (#306: low-credit notice, auto top-up), instead of
a beat scanning every balance. Changes that raise the balance queue it too: they re-arm the low-credit notice.
So do auto top-up config saves, which can make a charge due without the balance moving.
"""

from uuid import UUID

import structlog

from app.core import tasks

logger = structlog.get_logger()

# The balance net of open holds, which both rules use: a hold is mostly refunded when its run ends, so a
# chat turn's hold must neither send a low-credit notice nor charge the card; the spend does once it
# settles. ``{org}`` is the SQL expression for the organization id (not aliased ``bal`` or ``hold``).
NET_CREDITS_SQL = """(SELECT bal.credits_remaining + coalesce(sum(hold.amount), 0) FROM user_credits bal
    LEFT JOIN credit_transactions hold ON hold.organization_id = bal.organization_id AND hold.type = 'reservation'
     AND hold.status IN ('reserved', 'settling')
    WHERE bal.organization_id = {org} GROUP BY bal.credits_remaining)"""

# The ids of the organization ``{org}``'s admins (#299: they buy credits and see billing; roles are only
# 'admin' and 'member', #308).
ORG_ADMINS_SQL = """(SELECT "userId" FROM member WHERE "organizationId" = {org} AND role = 'admin')"""


def org_of(conn, meta: dict, user_id: str | None) -> str | None:
    """The organization a Stripe object pays for: its ``organization_id`` metadata, or for one made before
    #306 (only ``user_id``), the organization its user founded, whose wallet the object paid into then."""
    if meta.get('organization_id'):
        return meta['organization_id']
    if not user_id:
        return None
    row = conn.execute('SELECT own_organization(%s) AS id', (user_id,)).fetchone()
    return row['id'] and str(row['id'])


def buyer(conn, user_id: str | None) -> str | None:
    """``user_id`` when that account still exists, else None: a Stripe object outlives the member who made it,
    and the organization's ledger keeps the purchase with no user (#323)."""
    try:
        uid = UUID(str(user_id)) if user_id else None
    except ValueError:  # Stripe metadata is ours, but never trusted to be well formed
        return None
    if uid is None:
        return None
    row = conn.execute('SELECT id::text FROM "user" WHERE id = %s', (uid,)).fetchone()  # the primary key
    return row and row['id']


def changed(org_id, *, refused: bool = False) -> None:
    """Queue the balance rules for the wallet of ``org_id``. Call it after the commit, never inside the
    transaction. ``refused``: a hold was just refused for lack of available credits (see
    ``auto_topup.claim_due``). Never raises and never retries: a lost enqueue only delays the rules to the
    wallet's next change."""
    from app.worker import on_balance_changed

    try:
        # A beat, the back office or the Stripe webhook changes a wallet from a System scope
        with tasks.queued_for(str(org_id), 'balance changed'):
            on_balance_changed.apply_async((str(org_id), refused), retry=False)
    except Exception as exc:
        logger.error('billing.balance_enqueue_failed', org_id=str(org_id), error_type=type(exc).__name__)
