"""Plan credits: a monthly allowance that resets.

Each paid period opens with a 'stripe_plan_period' grant; an upgrade tops the
period's allowance up to the new plan as 'stripe_plan_upgrade'. Plan credits
are spent before purchased ones, so at the next period (or when the plan ends)
whatever is left of the allowance expires and top-ups survive.

Downgrades are scheduled at period end (``plan_change``): the period keeps the
allowance it paid for, and the renewal grants the lower plan's.

ponytail: while a renewal is unpaid (past_due) last month's leftover stays
usable; it expires when the invoice is paid or the subscription is deleted.
"""

import structlog

from app.billing.balance import NET_CREDITS_SQL

logger = structlog.get_logger()


def period(conn, org_id: str, *, lock: bool = True) -> dict | None:
    """Current plan period: allowance granted so far, credits bought since, and
    how much of the allowance is still unused. None before the first period.
    ``lock=False`` is for readers: they must not queue behind (or hold up) spending."""
    balance = conn.execute(
        'SELECT credits_remaining FROM user_credits WHERE organization_id = %s' + (' FOR UPDATE' if lock else ''),
        (org_id,),
    ).fetchone()
    start = conn.execute(
        """SELECT id, created_at, amount, balance_after FROM credit_transactions
           WHERE organization_id = %s AND reference_type = 'stripe_plan_period' ORDER BY created_at DESC LIMIT 1""",
        (org_id,),
    ).fetchone()
    if balance is None or start is None:
        return None
    since = conn.execute(
        """SELECT COALESCE(sum(amount) FILTER (WHERE reference_type IN ('stripe_plan_period', 'stripe_plan_upgrade')), 0) AS granted,
                  COALESCE(sum(amount) FILTER (WHERE type IN ('purchase', 'bonus', 'adjustment')), 0) AS bought
           FROM credit_transactions WHERE organization_id = %s AND created_at >= %s""",
        (org_id, start['created_at']),
    ).fetchone()
    # Everything that left the balance this period, whatever its kind (usage,
    # admin deductions, a previous expiry), counts against the allowance first.
    # An open hold is not spent yet, so it is added back (NET_CREDITS_SQL). A hold taken before the period
    # started belongs to the opening balance, whether it is still open or came back since: its refund is
    # not new allowance, and so is only what it held then, not credits it grew since (plan_expiry_owed.held).
    # What a renewal expired inside a hold (plan_expiry_owed.owed) is in neither the opening balance nor the net one.
    net = conn.execute(f'SELECT {NET_CREDITS_SQL.format(org="%s")} AS net', (org_id,)).fetchone()['net']
    pending = conn.execute(
        """SELECT COALESCE(sum(o.owed), 0) AS owed FROM plan_expiry_owed o JOIN credit_transactions h ON h.id = o.hold_id
           WHERE o.organization_id = %s AND h.type = 'reservation' AND h.status IN ('reserved', 'settling')""",
        (org_id,),
    ).fetchone()['owed']
    carried = conn.execute(
        """SELECT COALESCE(sum(COALESCE(o.held, h.amount) - COALESCE(o.owed, 0)), 0) AS held FROM credit_transactions h
           LEFT JOIN plan_expiry_owed o ON o.hold_id = h.id
           WHERE h.organization_id = %(org)s AND h.created_at < %(start)s AND h.id <> %(start_id)s AND (
             (h.type = 'reservation' AND h.status IN ('reserved', 'settling'))
             OR EXISTS (SELECT 1 FROM credit_transactions r WHERE r.organization_id = h.organization_id
                AND r.type = 'refund' AND r.created_at >= %(start)s
                AND (r.description = 'Released reservation ' || h.id::text
                     OR (r.reference_type = 'reservation.slack' AND r.reference_id = h.id::text))))""",
        {'org': org_id, 'start': start['created_at'], 'start_id': start['id']},
    ).fetchone()['held']
    net -= pending
    opening = start['balance_after'] - start['amount'] + carried
    used = opening + since['granted'] + since['bought'] - net
    unused = min(max(0, since['granted'] - used), net)
    return {'granted': since['granted'], 'unused': unused, 'balance': balance['credits_remaining']}


def expire_plan_credits(conn, payer: dict, reference_id: str) -> int:
    """Remove the unused part of the current period's allowance. Idempotent: a
    second call finds the allowance already used up by the first expiry.
    Credits in open holds are not spent yet and spending uses the allowance first, so they expire first: they are
    recorded on their hold and expire when it returns them. The wallet gives up the rest."""
    org_id = payer['organization_id']
    # Holds before the wallet, as every close takes them (grow_reservation documents the order).
    holds = conn.execute(
        """SELECT h.id, h.amount, COALESCE(o.owed, 0) AS owed FROM credit_transactions h
           LEFT JOIN plan_expiry_owed o ON o.hold_id = h.id
           WHERE h.organization_id = %s AND h.type = 'reservation' AND h.status IN ('reserved', 'settling')
           ORDER BY h.id FOR UPDATE OF h""",
        (org_id,),
    ).fetchall()
    current = period(conn, org_id)
    if current is None:
        return 0
    # A hold reserved after the lock above is missing from ``holds``: the wallet would owe more than it has. Fail
    # so that Stripe retries the event and sees it.
    open_ids = [
        r['id']
        for r in conn.execute(
            """SELECT id FROM credit_transactions WHERE organization_id = %s AND type = 'reservation'
           AND status IN ('reserved', 'settling') ORDER BY id""",
            (org_id,),
        ).fetchall()
    ]
    if open_ids != [h['id'] for h in holds]:
        raise RuntimeError(f'a hold opened during the plan expiry of {org_id}')
    # ponytail: the debt goes to holds in id order, not by who spends first; when the unused allowance is less than
    # what the holds keep, the hold that spends or returns first decides which credits expire. Per-hold shares
    # of the allowance would need the spend order of concurrent runs, which nothing records.
    owe = held_unused = min(current['unused'], sum(h['amount'] - h['owed'] for h in holds))
    for hold in holds:
        take = min(owe, hold['amount'] - hold['owed'])
        owe -= take
        conn.execute(
            """INSERT INTO plan_expiry_owed (hold_id, organization_id, held, owed) VALUES (%s, %s, %s, %s)
               ON CONFLICT (hold_id) DO UPDATE SET held = EXCLUDED.held, owed = plan_expiry_owed.owed + EXCLUDED.owed""",
            (hold['id'], org_id, hold['amount'], take),
        )
    expire = current['unused'] - held_unused
    if expire > 0:
        left = conn.execute(
            "SELECT deduct_credits(%s, %s, %s, 'plan_expiry', %s, NULL, NULL, 'Unused plan credits expired') AS left",
            (org_id, payer['user_id'], expire, reference_id),
        ).fetchone()['left']
        if left < 0:  # deduct_credits refuses more than the wallet holds
            raise RuntimeError(f'the plan expiry of {org_id} exceeds its wallet')
        logger.info('stripe.plan.expired', org_id=org_id, credits=expire)
    return expire
