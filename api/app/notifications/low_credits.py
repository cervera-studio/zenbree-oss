"""The low-credits alert (E3): told once per drop, on a balance change, to the admins who can buy credits."""

from app.billing.balance import NET_CREDITS_SQL, ORG_ADMINS_SQL
from app.billing.subscription import LIVE_STATUSES
from app.notifications.hrefs import billing_href
from app.notifications.service import LOW_CREDITS, Notified, notify, org_name


def low_credits(conn, org_id: str) -> list[Notified]:
    """Arm the organization's admins (#306: they buy credits) at or above LOW_CREDITS; notify (and disarm)
    them when armed and now below it, both on the wallet's balance net of open holds. With auto top-up on
    they stay armed and are never told: the top-up handles it. Run on every balance change
    (``app.billing.balance``); ``deliver`` the result after the commit, so a slow or failed send never rolls
    the disarm back into a resend."""
    # The flag is per admin and organization (#323): an admin of several is told about each one's drop.
    net, admins = NET_CREDITS_SQL.format(org='%(org)s'), ORG_ADMINS_SQL.format(org='%(org)s')
    params = {'org': org_id, 'low': LOW_CREDITS}
    conn.execute(
        f"""INSERT INTO low_credit_alerts AS s (organization_id, user_id, armed)
            SELECT %(org)s, a."userId", true FROM {admins} a WHERE {net} >= %(low)s
            ON CONFLICT (organization_id, user_id) DO UPDATE SET armed = true WHERE NOT s.armed""",
        params,
    )
    # One conditional UPDATE: of concurrent runs for the same drop, only the first finds an admin armed.
    # Only an admin ``notify`` will tell is disarmed (#487): a banned one stays armed and is told at the
    # first balance change after the ban ends.
    due = conn.execute(
        f"""UPDATE low_credit_alerts s SET armed = false
            WHERE s.organization_id = %(org)s AND s.user_id IN {admins} AND s.armed AND {net} < %(low)s
              AND zb.is_live_member(s.user_id, %(org)s)
              AND NOT EXISTS (SELECT 1 FROM auto_topups a WHERE a.organization_id = %(org)s AND a.enabled)
            RETURNING s.user_id::text, {net} AS credits""",
        params,
    ).fetchall()
    data = {'organization': org_name(conn, org_id)} if due else {}
    # #351: the live plan (``plan``: None when its price is not in BILLING_PLANS), and its next grant when one is
    # coming: a plan set to cancel by its renewal, or in arrears, grants nothing.
    sub = (
        due
        and conn.execute(
            """SELECT plan_id, status IN ('active', 'trialing')
                  AND (cancel_at IS NULL OR cancel_at > current_period_end)
                  AND current_period_end > now() AS renews, current_period_end
               FROM subscriptions WHERE organization_id = %s AND status = ANY(%s)
               ORDER BY updated_at DESC LIMIT 1""",
            (org_id, list(LIVE_STATUSES)),
        ).fetchone()
    )
    if sub:
        data['plan'] = sub['plan_id']
        if sub['renews'] and sub['plan_id']:
            data['renews_at'] = sub['current_period_end'].isoformat()
    told = (
        notify(
            conn,
            d['user_id'],
            'low_credits',
            href=billing_href(org_id),
            data={'credits': d['credits'], **data},
            org_id=org_id,
        )
        for d in due
    )
    return [n for n in told if n]  # only who was really told: the worker reports `notified` from this
