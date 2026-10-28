"""The wallet balance split into the plan's unused allowance, which resets at the period end, and the
credits bought, which never expire. Spending takes plan credits first (see ``plan_period``)."""

from app.billing.plan_period import period as plan_period
from app.core.db import get_conn


def credit_split(org_id: str, sub: dict | None) -> dict:
    """``credits_remaining`` with its split: ``plan_credits_left``, ``plan_credits_reset_at`` and
    ``purchased_credits``. Without a live plan (``sub``) the split is None and the whole balance is purchased.
    The balance and the split come from one read, so plan + purchased always equal ``credits_remaining``."""
    with get_conn() as conn:
        period = plan_period(conn, org_id, lock=False) if sub else None
        row = conn.execute(
            'SELECT credits_remaining FROM user_credits WHERE organization_id = %s', (org_id,)
        ).fetchone()
    balance = period['balance'] if period else row and row['credits_remaining']
    if balance is None or not sub:
        return {
            'credits_remaining': balance,
            'plan_credits_left': None,
            'plan_credits_reset_at': None,
            'purchased_credits': balance,
        }
    left = period['unused'] if period else 0
    return {
        'credits_remaining': balance,
        'plan_credits_left': left,
        'plan_credits_reset_at': sub['current_period_end'],
        'purchased_credits': balance - left,
    }
