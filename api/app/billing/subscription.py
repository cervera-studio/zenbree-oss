"""The organization's live plan, as the Stripe subscription webhook mirrors it into ``subscriptions``."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.core.db import get_conn

# Subscriptions in these states still own the organization's plan: no second checkout.
LIVE_STATUSES = ('active', 'trialing', 'past_due', 'unpaid', 'incomplete')


def latest_subscription(conn, org_id: str | UUID, live_only: bool = True) -> dict | None:
    """The organization's newest subscription; the admin also sees one that has ended (``live_only=False``)."""
    return conn.execute(
        """SELECT plan_id, scheduled_plan_id, status, current_period_end, cancel_at FROM subscriptions
           WHERE organization_id = %s AND (NOT %s OR status = ANY(%s)) ORDER BY updated_at DESC LIMIT 1""",
        (org_id, live_only, list(LIVE_STATUSES)),
    ).fetchone()


def _live_subscription(org_id: str) -> dict | None:
    with get_conn() as conn:
        return latest_subscription(conn, org_id)


class Subscription(BaseModel):
    plan_id: str | None
    scheduled_plan_id: str | None  # a downgrade that starts at current_period_end
    status: str
    current_period_end: datetime | None
    cancel_at: datetime | None  # when the plan ends; null while it renews (#538)


class MySubscription(BaseModel):
    subscription: Subscription | None
