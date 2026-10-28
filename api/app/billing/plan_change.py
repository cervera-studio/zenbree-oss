"""Plan changes (#417), in place of the Stripe portal's. An upgrade starts at once and charges the full
difference between the two plan prices, whatever the day of the period; a downgrade starts at the renewal.
The renewal date never moves. The webhook grants the credits (``routes._handle_invoice_paid``)."""

from typing import Literal

import stripe
import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.middleware import CurrentUser, require_org_admin
from app.billing import profile
from app.billing.routes import _plan_for_price, _plans
from app.billing.subscription import LIVE_STATUSES
from app.core.config import Plan, settings
from app.core.db import get_conn
from app.core.errors_http import api_error

logger = structlog.get_logger()
router = APIRouter(prefix='/v1/billing', tags=['billing'])


class PlanChangeIn(BaseModel):
    plan_id: str


class PlanChange(BaseModel):
    result: Literal['upgraded', 'payment_required', 'downgrade_scheduled', 'downgrade_canceled']
    payment_url: str | None  # payment_required: Stripe's page that takes the difference (card declined, 3-D Secure)


@router.post('/plan', response_model=PlanChange)
def change_plan(body: PlanChangeIn, user: CurrentUser = Depends(require_org_admin)) -> dict:
    target = next((p for p in _plans() if p.id == body.plan_id), None)
    if target is None:
        raise api_error(404, 'BILLING.UNKNOWN_PLAN', f'Unknown plan {body.plan_id!r}')
    try:
        with profile.locked(user.org_id) as conn:  # a double click must not charge twice
            return _change(conn, _subscription_id(conn, user.org_id), target)
    except stripe.StripeError as e:
        logger.error('stripe.plan_change.failed', error_type=type(e).__name__, plan_id=target.id)
        raise api_error(502, 'BILLING.PLAN_CHANGE_FAILED', 'Could not change the plan') from e


def _subscription_id(conn, org_id: str) -> str:
    sub = conn.execute(
        """SELECT stripe_subscription_id FROM subscriptions
           WHERE organization_id = %s AND status = ANY(%s) ORDER BY updated_at DESC LIMIT 1""",
        (org_id, list(LIVE_STATUSES)),
    ).fetchone()
    if sub is None:
        raise api_error(409, 'BILLING.NO_SUBSCRIPTION', 'Subscribe to a plan first')
    return sub['stripe_subscription_id']


def _unavailable(conn, sub) -> bool:
    """Judged on Stripe's subscription, not on the mirror, which a webhook may not have updated yet.
    A cancelled plan (``cancel_at`` in flexible billing mode) is resumed in the portal first: a downgrade
    schedule would renew it. A renewal still a draft (its first hour), unpaid, or paid but not yet credited
    here (a late webhook) opens the period the upgrade's credits belong to: it comes first, so they are
    not granted to the old period and then expired. A schedule may bill that renewal as an update; only an
    upgrade's invoice credits a plan back."""
    invoice = sub.latest_invoice
    renewal_pending = isinstance(invoice, stripe.Invoice) and (
        invoice.status in ('draft', 'open')
        or (
            invoice.status == 'paid'
            and not any(line.amount < 0 for line in invoice.lines.data)
            and not conn.execute('SELECT 1 FROM purchases WHERE stripe_invoice_id = %s', (invoice.id,)).fetchone()
        )
    )
    return sub.status != 'active' or bool(sub.cancel_at_period_end or sub.cancel_at) or renewal_pending


def _change(conn, sub_id: str, target: Plan) -> dict:
    sub = stripe.Subscription.retrieve(sub_id, api_key=settings.stripe_secret_key, expand=['latest_invoice'])
    if sub.pending_update:  # an upgrade still waits for its payment: that invoice, never a second one
        return _payment_required(sub)
    item = sub['items']['data'][0]
    current = _plan_for_price(item['price']['id'])
    if current is None or _unavailable(conn, sub):
        raise api_error(409, 'BILLING.PLAN_CHANGE_UNAVAILABLE', 'The current plan cannot change now')
    if sub.schedule:  # a scheduled downgrade: every change replaces it
        stripe.SubscriptionSchedule.release(sub.schedule, api_key=settings.stripe_secret_key)
        _set_scheduled(sub_id, None)
        if target.id == current.id:
            return {'result': 'downgrade_canceled', 'payment_url': None}
    if target.id == current.id:
        raise api_error(409, 'BILLING.SAME_PLAN', 'The organization is on this plan already')
    if target.price_eur_cents > current.price_eur_cents:
        return _upgrade(sub_id, item, target)
    return _downgrade(sub_id, item, target)


def _upgrade(sub_id: str, item, target: Plan) -> dict:
    # Prorating from the period start credits the whole old price and charges the whole new one, so the
    # invoice is the price difference on any day. pending_if_incomplete: the plan changes once it is paid.
    sub = stripe.Subscription.modify(
        sub_id,
        api_key=settings.stripe_secret_key,
        items=[{'id': item['id'], 'price': target.stripe_price_id}],
        proration_behavior='always_invoice',
        proration_date=item['current_period_start'],
        payment_behavior='pending_if_incomplete',
        expand=['latest_invoice'],
    )
    return _payment_required(sub) if sub.pending_update else {'result': 'upgraded', 'payment_url': None}


def _payment_required(sub) -> dict:
    invoice = sub.latest_invoice  # expanded: the open invoice of the difference
    url = invoice.hosted_invoice_url if isinstance(invoice, stripe.Invoice) else None
    return {'result': 'payment_required', 'payment_url': url}


def _downgrade(sub_id: str, item, target: Plan) -> dict:
    """The current price until the period ends, then the target's; the schedule then lets go of the
    subscription. The period keeps the allowance it paid for, and the renewal grants the target's."""
    key = settings.stripe_secret_key
    schedule = stripe.SubscriptionSchedule.create(api_key=key, from_subscription=sub_id)
    phase = schedule.phases[0]
    stripe.SubscriptionSchedule.modify(
        schedule.id,
        api_key=key,
        end_behavior='release',
        proration_behavior='none',
        phases=[
            {
                'items': [{'price': item['price']['id'], 'quantity': 1}],
                'start_date': phase.start_date,
                'end_date': phase.end_date,
                'automatic_tax': {'enabled': True},
            },
            {
                'items': [{'price': target.stripe_price_id, 'quantity': 1}],
                'duration': {'interval': 'month'},
                'automatic_tax': {'enabled': True},  # VAT, as every subscription Checkout sets it
            },
        ],
    )
    _set_scheduled(sub_id, target.id)
    return {'result': 'downgrade_scheduled', 'payment_url': None}


def _set_scheduled(sub_id: str, plan_id: str | None) -> None:
    # Its own transaction: Stripe changed already, so a later failure must not roll this back.
    # ponytail: a release's late webhook (no schedule) can clear a downgrade scheduled again seconds
    # later; the renewal still downgrades, and choosing the plan again shows it. Match schedule ids if it bites.
    with get_conn() as conn:
        conn.execute(
            'UPDATE subscriptions SET scheduled_plan_id = %s WHERE stripe_subscription_id = %s', (plan_id, sub_id)
        )
