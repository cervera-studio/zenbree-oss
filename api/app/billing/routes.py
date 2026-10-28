"""Stripe billing: one-off credit packs and monthly plans via hosted Checkout,
the customer portal, and the ledger. All of it belongs to the organization
(#306) and only its admins reach it; ``user_id`` records who did it.

Every payment collects VAT (Stripe Tax, automatic_tax) and the buyer's VAT id,
and produces a Stripe invoice. Adapted from the earlier implementation billing (stripe_adapter,
process_webhook) minus tiers, access codes and the event bus.
"""

import asyncio
import json
from datetime import datetime
from typing import Literal
from uuid import UUID

import stripe
import structlog
from fastapi import APIRouter, Depends, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel
from stripe.params import CustomerCreateParams

from app.auth.middleware import CurrentUser, SystemReason, public, require_org_admin
from app.billing import auto_topup, balance, plan_period, profile
from app.billing.balance import ORG_ADMINS_SQL
from app.billing.subscription import MySubscription, _live_subscription
from app.core.config import settings
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.notifications import service as notifications
from app.notifications.hrefs import billing_href

logger = structlog.get_logger()
router = APIRouter(prefix='/v1/billing', tags=['billing'])


def _packs():
    return [p for p in settings.billing_packs if p.stripe_price_id]


def _plans():
    return [p for p in settings.billing_plans if p.stripe_price_id]


class Offer(BaseModel):
    id: str
    credits: int
    price_eur_cents: int


class Packs(BaseModel):
    packs: list[Offer]


class Plans(BaseModel):
    plans: list[Offer]


@router.get('/packs', response_model=Packs, dependencies=[public(None)])
def list_packs() -> dict:
    return {'packs': [{'id': p.id, 'credits': p.credits, 'price_eur_cents': p.price_eur_cents} for p in _packs()]}


@router.get('/plans', response_model=Plans, dependencies=[public(None)])
def list_plans() -> dict:
    return {'plans': [{'id': p.id, 'credits': p.credits, 'price_eur_cents': p.price_eur_cents} for p in _plans()]}


@router.get('/subscription', response_model=MySubscription)
def my_subscription(user: CurrentUser = Depends(require_org_admin)) -> dict:
    return {'subscription': _live_subscription(user.org_id)}


_CUSTOMER_SQL = 'SELECT stripe_customer_id FROM billing_customers WHERE organization_id = %s'


def _stored_customer_id(org_id: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(_CUSTOMER_SQL, (org_id,)).fetchone()
    return row['stripe_customer_id'] if row else None


def _customer_id(user: CurrentUser) -> str:
    """The organization's Stripe Customer, created on first use (billed to the admin's email)."""
    if customer_id := _stored_customer_id(user.org_id):
        return customer_id
    # The idempotency key makes two concurrent first checkouts get the same customer.
    params: CustomerCreateParams = {
        'api_key': settings.stripe_secret_key,
        'metadata': {'organization_id': user.org_id, 'user_id': user.id},
        'idempotency_key': f'zenbree-customer-{user.org_id}',
    }
    if user.email:
        params['email'] = user.email
    customer = stripe.Customer.create(**params)
    with get_conn() as conn:
        conn.execute(
            'INSERT INTO billing_customers (organization_id, user_id, stripe_customer_id, email)'
            ' VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING',  # email: what Stripe was given (#499)
            (user.org_id, user.id, customer.id, user.email or None),
        )
        return conn.execute(_CUSTOMER_SQL, (user.org_id,)).fetchone()['stripe_customer_id']


def _checkout(user: CurrentUser, **params) -> dict:
    """Checkout uses the saved customer profile and Stripe Tax calculation."""
    try:
        with profile.locked(user.org_id) as conn:
            data = profile.required(conn, user.org_id)
            customer = _customer_id(user)
            synced = profile.sync_customer(conn, user.org_id, customer, data)
            if synced:
                session = stripe.checkout.Session.create(
                    api_key=settings.stripe_secret_key,
                    customer=customer,
                    client_reference_id=user.id,
                    automatic_tax={'enabled': True},
                    tax_id_collection={'enabled': False},
                    billing_address_collection='auto',
                    success_url=f'{settings.web_origin}/billing?checkout=success',
                    cancel_url=f'{settings.web_origin}/billing?checkout=cancel',
                    **params,
                )
    except stripe.StripeError as e:
        logger.error('stripe.checkout.failed', error_type=type(e).__name__, mode=params.get('mode'))
        raise api_error(502, 'BILLING.CHECKOUT_FAILED', 'Could not start checkout') from e
    if not synced:
        raise profile.sync_error()
    return {'url': session.url}


class CheckoutIn(BaseModel):
    pack_id: str


class RedirectUrl(BaseModel):
    url: str


@router.post('/checkout', response_model=RedirectUrl)
def checkout(body: CheckoutIn, user: CurrentUser = Depends(require_org_admin)) -> dict:
    pack = next((p for p in _packs() if p.id == body.pack_id), None)
    if pack is None:
        raise api_error(404, 'BILLING.UNKNOWN_PACK', f'Unknown pack {body.pack_id!r}')
    return _checkout(
        user,
        mode='payment',
        line_items=[{'price': pack.stripe_price_id, 'quantity': 1}],
        invoice_creation={'enabled': True},  # B2B buyers get a VAT invoice for top-ups too
        payment_intent_data={'setup_future_usage': 'off_session'},  # the card can fund auto top-ups
        metadata={
            'organization_id': user.org_id,
            'user_id': user.id,
            'pack_id': pack.id,
            'credits': str(pack.credits),
        },
    )


class SubscribeIn(BaseModel):
    plan_id: str


@router.post('/subscribe', response_model=RedirectUrl)
def subscribe(body: SubscribeIn, user: CurrentUser = Depends(require_org_admin)) -> dict:
    plan = next((p for p in _plans() if p.id == body.plan_id), None)
    if plan is None:
        raise api_error(404, 'BILLING.UNKNOWN_PLAN', f'Unknown plan {body.plan_id!r}')
    if _live_subscription(user.org_id):
        raise api_error(409, 'BILLING.ALREADY_SUBSCRIBED', 'Change the current plan instead (POST /v1/billing/plan)')
    return _checkout(
        user,
        mode='subscription',
        line_items=[{'price': plan.stripe_price_id, 'quantity': 1}],
        subscription_data={'metadata': {'organization_id': user.org_id, 'user_id': user.id}},
        metadata={'organization_id': user.org_id, 'user_id': user.id, 'plan_id': plan.id},
    )


@router.post('/portal', response_model=RedirectUrl)
def portal(user: CurrentUser = Depends(require_org_admin)) -> dict:
    customer = _stored_customer_id(user.org_id)
    if customer is None:
        raise api_error(404, 'BILLING.NO_CUSTOMER', 'No billing account yet: buy a pack or a plan first')
    try:
        session = stripe.billing_portal.Session.create(
            api_key=settings.stripe_secret_key, customer=customer, return_url=f'{settings.web_origin}/billing'
        )
    except stripe.StripeError as e:
        logger.error('stripe.portal.failed', error_type=type(e).__name__)
        raise api_error(502, 'BILLING.PORTAL_FAILED', 'Could not open the billing portal') from e
    return {'url': session.url}


# ── Auto top-up (#94) ──────────────────────────────────────────────────────


def _auto_topup_state(org_id: str) -> dict:
    packs = _packs()
    cfg = auto_topup.get_config(org_id) or {
        'enabled': False,
        'pack_id': packs[0].id if packs else None,
        'threshold': auto_topup.THRESHOLDS[0],
        'last_error': None,
    }
    return {**cfg, 'thresholds': list(auto_topup.THRESHOLDS)}


class AutoTopup(BaseModel):
    enabled: bool
    pack_id: str | None
    threshold: int
    last_error: str | None
    thresholds: list[int]


class AutoTopupSaved(AutoTopup):
    setup_url: str | None


@router.get('/auto-topup', response_model=AutoTopup)
def get_auto_topup(user: CurrentUser = Depends(require_org_admin)) -> dict:
    return _auto_topup_state(user.org_id)


class AutoTopupIn(BaseModel):
    enabled: bool
    pack_id: str
    threshold: int


@router.put('/auto-topup', response_model=AutoTopupSaved)
def put_auto_topup(body: AutoTopupIn, user: CurrentUser = Depends(require_org_admin)) -> dict:
    """Save the settings. Turning it on without a saved card returns ``setup_url``:
    a Checkout that saves one, after which the webhook switches it on."""
    if not any(p.id == body.pack_id for p in _packs()):
        raise api_error(404, 'BILLING.UNKNOWN_PACK', f'Unknown pack {body.pack_id!r}')
    if body.threshold not in auto_topup.THRESHOLDS:
        raise api_error(422, 'BILLING.INVALID_THRESHOLD', f'Threshold must be one of {list(auto_topup.THRESHOLDS)}')
    setup_url = None
    synced = True
    try:
        with profile.locked(user.org_id) as conn:
            if body.enabled:
                data = profile.required(conn, user.org_id)
                customer = _customer_id(user)
                synced = profile.sync_customer(conn, user.org_id, customer, data)
                if synced and auto_topup.payment_method(customer) is None:
                    setup_url = stripe.checkout.Session.create(
                        api_key=settings.stripe_secret_key,
                        mode='setup',
                        currency='eur',
                        allowed_payment_method_types=['card'],
                        customer=customer,
                        client_reference_id=user.id,
                        metadata={'organization_id': user.org_id, 'user_id': user.id, 'kind': auto_topup.KIND},
                        billing_address_collection='auto',
                        tax_id_collection={'enabled': False},
                        success_url=f'{settings.web_origin}/billing?autotopup=saved',
                        cancel_url=f'{settings.web_origin}/billing?autotopup=cancel',
                    ).url
            if synced:
                auto_topup.save_config(
                    user.org_id,
                    user.id,
                    enabled=body.enabled and setup_url is None,
                    awaiting_card=setup_url is not None,
                    pack_id=body.pack_id,
                    threshold=body.threshold,
                )
    except stripe.StripeError as exc:
        logger.error('stripe.auto_topup_setup.failed', error_type=type(exc).__name__)
        raise api_error(502, 'BILLING.CHECKOUT_FAILED', 'Could not check the saved card') from exc
    if not synced:
        raise profile.sync_error()
    balance.changed(user.org_id)  # turned on under the threshold, or off while low: due now
    return {**_auto_topup_state(user.org_id), 'setup_url': setup_url}


# ── Webhook ────────────────────────────────────────────────────────────────


def _net_cents(total, tax) -> int:
    """Revenue excludes VAT: it is owed to the tax office, not ours."""
    return (total or 0) - (tax or 0)


def _handle_session_paid(conn, s: dict) -> None:
    meta = s.get('metadata') or {}
    org_id = balance.org_of(conn, meta, s.get('client_reference_id') or meta.get('user_id'))
    user_id = balance.buyer(conn, s.get('client_reference_id') or meta.get('user_id'))
    if s.get('mode') == 'setup':
        if meta.get('kind') == auto_topup.KIND and org_id:
            auto_topup.enable_after_setup(conn, org_id)
        return
    if s.get('mode') == 'subscription':
        return  # plan credits come with invoice.paid, the same path as renewals
    if s.get('payment_status') != 'paid':
        return  # async methods: credited on checkout.session.async_payment_succeeded
    if not org_id or not meta.get('credits'):
        logger.error('stripe.checkout.missing_data', session_id=s['id'])
        return
    credits = int(meta['credits'])
    row = conn.execute(
        """INSERT INTO purchases (organization_id, user_id, stripe_session_id, stripe_payment_intent, pack_id,
                                  credits, amount_cents, currency)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (stripe_session_id) DO NOTHING RETURNING id""",
        (
            org_id,
            user_id,
            s['id'],
            s.get('payment_intent'),
            meta.get('pack_id', '?'),
            credits,
            _net_cents(s.get('amount_total'), (s.get('total_details') or {}).get('amount_tax')),
            s.get('currency') or 'eur',
        ),
    ).fetchone()
    if row is None:
        return  # this session was already credited by another event
    conn.execute(
        "SELECT increment_credits(%s, %s, %s, 'purchase', 'stripe_checkout', %s, NULL, NULL, %s)",
        (org_id, user_id, credits, s['id'], f'Top-up {meta.get("pack_id")}'),
    )
    logger.info('stripe.topup.credited', org_id=org_id, user_id=user_id, credits=credits)


def _customer(conn, customer_id: str | None) -> dict | None:
    """The organization a Stripe customer bills, and the user who created it (recorded as who bought)."""
    return conn.execute(
        'SELECT organization_id::text, user_id::text FROM billing_customers WHERE stripe_customer_id = %s',
        (customer_id,),
    ).fetchone()


def _plan_for_price(price_id: str | None):
    return next((p for p in settings.billing_plans if p.stripe_price_id and p.stripe_price_id == price_id), None)


def _line_plan(line: dict):
    return _plan_for_price(((line.get('pricing') or {}).get('price_details') or {}).get('price'))


def _handle_invoice_paid(conn, inv: dict) -> list[notifications.Notified | None]:
    """Credit a paid invoice. Returns what to ``deliver`` once the transaction commits."""
    if (inv.get('metadata') or {}).get('kind') == auto_topup.KIND:
        auto_topup.credit_invoice(conn, inv)  # usually credited already by the charge itself
        return []
    reason = inv.get('billing_reason')
    if reason not in ('subscription_create', 'subscription_cycle', 'subscription_update'):
        return []  # top-up invoices are credited by checkout.session.*
    payer = _customer(conn, inv.get('customer'))
    lines = inv['lines']['data']
    old = next((p for line in lines if line.get('amount', 0) < 0 and (p := _line_plan(line))), None)
    if reason == 'subscription_update' and old:
        # Upgrade (plan_change): the new price is charged (amount > 0), the old one
        # credited back (amount < 0). The period's allowance is topped up to
        # the new plan, so switching back and forth never grants twice.
        new = next((p for line in lines if line.get('amount', 0) > 0 and (p := _line_plan(line))), None)
        period = payer and plan_period.period(conn, payer['organization_id'])
        granted = period['granted'] if period else old.credits
        plan, credits, ref_type = new, (new.credits - granted if new else 0), 'stripe_plan_upgrade'
        if new and credits <= 0:
            return []  # downgrade, or this period already has the new plan's allowance
        paid = sum(line['amount'] for line in lines if _line_plan(line))
        if new and paid < new.price_eur_cents - old.price_eur_cents:
            # #417: a prorated switch (the portal's) pays a fraction of the difference, so no credits.
            # ponytail: list prices; a coupon makes a paid upgrade look underpaid (logged): add back discount_amounts.
            logger.error('stripe.plan.upgrade_underpaid', invoice_id=inv['id'], customer=inv.get('customer'))
            return []
    else:  # a new period; a schedule's phase change may bill it as subscription_update, with no credit line
        plan = next((p for p in map(_line_plan, lines) if p), None)
        credits, ref_type = plan and plan.credits, 'stripe_plan_period'
    if not payer or not plan or not credits:
        logger.error('stripe.invoice.unmapped', invoice_id=inv['id'], customer=inv.get('customer'), reason=reason)
        return []
    row = conn.execute(
        """INSERT INTO purchases (organization_id, user_id, stripe_invoice_id, pack_id, credits, amount_cents, currency)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (stripe_invoice_id) DO NOTHING RETURNING id""",
        (
            payer['organization_id'],
            payer['user_id'],
            inv['id'],
            f'plan:{plan.id}',
            credits,
            inv.get('total_excluding_tax') or 0,
            inv.get('currency') or 'eur',
        ),
    ).fetchone()
    if row is None:
        return []
    expired = 0
    if ref_type == 'stripe_plan_period':  # last period's leftover goes before the new allowance
        expired = plan_period.expire_plan_credits(conn, payer, inv['id'])
    conn.execute(
        "SELECT increment_credits(%s, %s, %s, 'renewal', %s, %s, NULL, NULL, %s)",
        (payer['organization_id'], payer['user_id'], credits, ref_type, inv['id'], f'Plan {plan.id}'),
    )
    logger.info('stripe.plan.credited', org_id=payer['organization_id'], credits=credits, reason=reason)
    if reason == 'subscription_create' or ref_type == 'stripe_plan_upgrade':
        return []  # creating or upgrading a plan is the admin's own action: the page's toast is enough
    org = payer['organization_id']
    admins = conn.execute(f'SELECT a."userId"::text AS id FROM {ORG_ADMINS_SQL.format(org="%s")} a', (org,)).fetchall()
    data = {'credits': credits, 'plan': plan.id, 'expired': expired, 'organization': notifications.org_name(conn, org)}
    return [
        notifications.notify(conn, a['id'], 'plan_renewed', href=billing_href(str(org)), data=data, org_id=str(org))
        for a in admins
    ]


def _handle_subscription(conn, sub: dict, event_created: int, ended: bool = False) -> None:
    payer = _customer(conn, sub.get('customer'))
    if not payer:
        logger.error('stripe.subscription.unknown_customer', subscription_id=sub['id'], customer=sub.get('customer'))
        return
    item = sub['items']['data'][0]
    plan = _plan_for_price(item['price']['id'])
    conn.execute(
        """INSERT INTO subscriptions (stripe_subscription_id, organization_id, user_id, plan_id, status,
                                      current_period_end, cancel_at_period_end, cancel_at, event_created)
           VALUES (%s, %s, %s, %s, %s, to_timestamp(%s), %s, to_timestamp(%s), %s)
           ON CONFLICT (stripe_subscription_id) DO UPDATE SET
               plan_id = EXCLUDED.plan_id, status = EXCLUDED.status,
               current_period_end = EXCLUDED.current_period_end,
               cancel_at_period_end = EXCLUDED.cancel_at_period_end, cancel_at = EXCLUDED.cancel_at,
               event_created = EXCLUDED.event_created, updated_at = now(),
               -- the downgrade started, or its schedule is gone (cancelled, released in the dashboard)
               scheduled_plan_id = CASE WHEN %s THEN NULLIF(subscriptions.scheduled_plan_id, EXCLUDED.plan_id) END
           WHERE subscriptions.event_created <= EXCLUDED.event_created""",
        (
            sub['id'],
            payer['organization_id'],
            payer['user_id'],
            plan and plan.id,
            sub['status'],
            item.get('current_period_end'),
            bool(sub.get('cancel_at_period_end')),
            # Stripe sets cancel_at for a period-end cancellation too; the period end covers an event without it
            sub.get('cancel_at') or (item.get('current_period_end') if sub.get('cancel_at_period_end') else None),
            event_created,
            bool(sub.get('schedule')),
        ),
    )
    if ended:
        plan_period.expire_plan_credits(conn, payer, sub['id'])  # the plan is over: its allowance goes too


def _handle_refund(conn, charge: dict) -> None:
    # ponytail: no automatic claw-back (credits may be spent already); the
    # refund shows in admin costs and the admin adjusts credits by hand.
    # Top-ups match on their payment_intent. Subscription invoices don't carry
    # one: Stripe pairs invoice and payment_intent in invoice_payment.paid,
    # which webhook_events keeps, so the refund finds its invoice through it.
    pi = charge.get('payment_intent')
    conn.execute(
        """UPDATE purchases SET refunded_cents = %s
           WHERE stripe_payment_intent = %s
              OR stripe_invoice_id = (SELECT payload->'data'->'object'->>'invoice' FROM webhook_events
                                      WHERE event_type = 'invoice_payment.paid'
                                        AND payload->'data'->'object'->'payment'->>'payment_intent' = %s
                                      LIMIT 1)""",
        (charge.get('amount_refunded') or 0, pi, pi),
    )
    logger.warning(
        'stripe.charge.refunded',
        payment_intent=charge.get('payment_intent'),
        amount_refunded=charge.get('amount_refunded'),
    )


def process_event(event: dict) -> str:
    """Apply one verified event exactly once. Returns 'processed' | 'duplicate'."""
    event_id, event_type = event['id'], event['type']
    pending: list[notifications.Notified | None] = []
    try:
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO webhook_events (stripe_event_id, event_type, payload)
                   VALUES (%s, %s, %s) ON CONFLICT (stripe_event_id) DO NOTHING""",
                (event_id, event_type, Jsonb(event)),
            )
            # Row lock serialises concurrent deliveries of the same event.
            status = conn.execute(
                'SELECT status FROM webhook_events WHERE stripe_event_id = %s FOR UPDATE', (event_id,)
            ).fetchone()['status']
            if status == 'processed':
                return 'duplicate'
            obj = event['data']['object']
            match event_type:
                case 'checkout.session.completed' | 'checkout.session.async_payment_succeeded':
                    _handle_session_paid(conn, obj)
                case 'invoice.paid':
                    pending = _handle_invoice_paid(conn, obj)
                case 'customer.subscription.created' | 'customer.subscription.updated':
                    _handle_subscription(conn, obj, event['created'])
                case 'customer.subscription.deleted':
                    _handle_subscription(conn, obj, event['created'], ended=True)
                case 'invoice_payment.paid':
                    pass  # kept in webhook_events: maps a refund's payment_intent to its invoice
                case 'charge.refunded':
                    _handle_refund(conn, obj)
                case _:
                    logger.info('stripe.webhook.unhandled', event_type=event_type)
            # Whose balance (or auto top-up switch) the event may have changed, to queue its rules after the commit.
            meta = obj.get('metadata') or {}
            payer = obj.get('customer') and _customer(conn, obj['customer'])
            org_id = (
                meta.get('organization_id')
                or (payer and payer['organization_id'])
                or balance.org_of(conn, meta, obj.get('client_reference_id') or meta.get('user_id'))
            )
            conn.execute(
                "UPDATE webhook_events SET status = 'processed', processed_at = now(), error_message = NULL "
                'WHERE stripe_event_id = %s',
                (event_id,),
            )
    except Exception as e:
        # Everything above rolled back; record the failure outside it.
        logger.error('stripe.webhook.failed', event_id=event_id, event_type=event_type, error=str(e)[:500])
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO webhook_events (stripe_event_id, event_type, payload, status, error_message)
                   VALUES (%s, %s, %s, 'failed', %s)
                   ON CONFLICT (stripe_event_id) DO UPDATE SET status = 'failed', error_message = EXCLUDED.error_message""",
                (event_id, event_type, Jsonb(event), str(e)[:1000]),
            )
        raise
    for notified in pending:
        notifications.deliver(notified)
    if org_id:
        balance.changed(org_id)
    return 'processed'


class WebhookReceipt(BaseModel):
    received: bool
    result: Literal['processed', 'duplicate']


@router.post('/stripe-webhook', response_model=WebhookReceipt, dependencies=[public(SystemReason.STRIPE_WEBHOOK)])
async def stripe_webhook(request: Request) -> dict:
    payload = await request.body()
    try:
        stripe.Webhook.construct_event(
            payload, request.headers.get('stripe-signature', ''), settings.stripe_webhook_secret
        )
        event = json.loads(payload)  # verified; plain dicts for the JSONB log
    except ValueError, stripe.SignatureVerificationError:
        raise api_error(400, 'BILLING.WEBHOOK_INVALID_SIG', 'Invalid Stripe signature') from None
    try:
        result = await asyncio.to_thread(process_event, event)
    except Exception as e:
        # 500 → Stripe retries with backoff; the failed row is in webhook_events.
        raise api_error(500, 'BILLING.WEBHOOK_HANDLER_FAILED', 'Webhook handler failed') from e
    return {'received': True, 'result': result}


# ── The organization's ledger ──────────────────────────────────────────────


class Transaction(BaseModel):
    id: UUID
    type: Literal['deduction', 'refund', 'renewal', 'purchase', 'bonus', 'adjustment', 'reservation']
    status: Literal['committed', 'reserved', 'released', 'settling']
    amount: int
    balance_after: int
    description: str | None
    tool_name: str | None
    reference_type: str | None
    created_at: datetime
