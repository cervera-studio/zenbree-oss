"""Auto top-up (#94): when the organization's balance drops under its threshold,
buy the chosen pack off-session with the card saved on its Stripe customer (#306).

Each top-up is a Stripe invoice (Stripe Tax, VAT id, PDF) like a Checkout
purchase. ``charging_since`` lets one charge per wallet run at a time; there is
no daily cap (owner decision on #94). A failed charge turns auto top-up off
and keeps the reason in ``last_error``: retrying a declined card on every
balance change helps nobody.
"""

from __future__ import annotations

import stripe
import structlog

from app.billing import balance
from app.core.config import settings
from app.core.db import get_conn
from app.notifications import service as notifications
from app.notifications.hrefs import billing_href

logger = structlog.get_logger()

# Balance thresholds the user picks from; each is at most the smallest pack, so
# one top-up always lifts the balance back over it.
THRESHOLDS = (100, 250, 500, 1000)
KIND = 'auto_topup'  # metadata.kind on the invoice and on the card setup session


def get_config(org_id: str) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            'SELECT enabled, pack_id, threshold, last_error FROM auto_topups WHERE organization_id = %s', (org_id,)
        ).fetchone()


def save_config(org_id: str, user_id: str, *, enabled: bool, awaiting_card: bool, pack_id: str, threshold: int) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO auto_topups (organization_id, user_id, enabled, awaiting_card, pack_id, threshold)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (organization_id) DO UPDATE SET user_id = EXCLUDED.user_id, enabled = EXCLUDED.enabled,
                   awaiting_card = EXCLUDED.awaiting_card, pack_id = EXCLUDED.pack_id, threshold = EXCLUDED.threshold,
                   last_error = NULL, updated_at = now()""",
            (org_id, user_id, enabled, awaiting_card, pack_id, threshold),
        )


def enable_after_setup(conn, org_id: str) -> None:
    """The card setup Checkout finished: switch on, unless an admin saved it off meanwhile."""
    conn.execute(
        """UPDATE auto_topups SET enabled = true, awaiting_card = false, last_error = NULL, updated_at = now()
           WHERE organization_id = %s AND awaiting_card""",
        (org_id,),
    )


def payment_method(customer_id: str) -> str | None:
    """The customer's default card for invoices, else the newest saved card."""
    # Cards only: a SEPA mandate settles days later, and a failure then would leave the claim set.
    customer = stripe.Customer.retrieve(
        customer_id, api_key=settings.stripe_secret_key, expand=['invoice_settings.default_payment_method']
    )
    default = (customer.to_dict().get('invoice_settings') or {}).get('default_payment_method')
    if isinstance(default, dict) and default.get('type') == 'card':
        return default['id']
    cards = stripe.Customer.list_payment_methods(customer_id, api_key=settings.stripe_secret_key, type='card', limit=1)
    return cards.data[0].id if cards.data else None


def claim_due(org_id: str, *, refused: bool = False) -> list[dict]:
    """The organization, marked as charging, when under its threshold with no charge running; else [].

    The balance is net of open holds, so a hold that is then refunded never charges the card. After a
    refused hold (``refused``) it is the available balance instead: a long job's holds can take it to 0
    while the net stays over the threshold, and the job needs the credits now."""
    # ponytail: the available balance is read when the task runs; a refusal whose holds settled meanwhile
    # is judged on the settled balance, which is the right answer anyway.
    credits = (
        '(SELECT credits_remaining FROM user_credits WHERE organization_id = a.organization_id)'
        if refused
        else balance.NET_CREDITS_SQL.format(org='a.organization_id')
    )
    with get_conn() as conn:
        stuck = conn.execute(
            "SELECT 1 FROM auto_topups WHERE organization_id = %s AND charging_since < now() - interval '1 hour'",
            (org_id,),
        ).fetchone()
        if stuck:
            # ponytail: a worker that died mid-charge leaves the claim set; releasing it blindly could
            # charge twice (the first invoice may have been paid). Logged for an admin to check in Stripe.
            logger.error('billing.auto_topup.stuck', org_ids=[org_id])
        return conn.execute(
            f"""UPDATE auto_topups a SET charging_since = now()
                WHERE a.organization_id = %s AND a.enabled AND a.charging_since IS NULL
                  AND {credits} < a.threshold
                RETURNING a.organization_id::text, a.pack_id, a.charging_since""",
            (org_id,),
        ).fetchall()


def credit_invoice(conn, inv: dict) -> None:
    """Credit a paid auto top-up invoice once (charge path and invoice.paid both land here)."""
    meta = inv.get('metadata') or {}
    credits = int(meta.get('credits') or 0)
    org_id = balance.org_of(conn, meta, meta.get('user_id'))
    user_id = balance.buyer(conn, meta.get('user_id'))
    if not org_id or credits <= 0:
        logger.error('billing.auto_topup.unmapped_invoice', invoice_id=inv['id'])
        return
    row = conn.execute(
        """INSERT INTO purchases (organization_id, user_id, stripe_invoice_id, pack_id, credits, amount_cents, currency)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (stripe_invoice_id) DO NOTHING RETURNING id""",
        (
            org_id,
            user_id,
            inv['id'],
            meta.get('pack_id', '?'),
            credits,
            inv.get('total_excluding_tax') or 0,
            inv.get('currency') or 'eur',
        ),
    ).fetchone()
    if row is None:
        return  # credited already: a late replay must not release a newer charge's claim
    conn.execute(
        "SELECT increment_credits(%s, %s, %s, 'purchase', 'stripe_auto_topup', %s, NULL, NULL, %s)",
        (org_id, user_id, credits, inv['id'], f'Auto top-up {meta.get("pack_id")}'),
    )
    conn.execute('UPDATE auto_topups SET charging_since = NULL WHERE organization_id = %s', (org_id,))
    logger.info('billing.auto_topup.credited', org_id=org_id, credits=credits)


def _holds_claim(org_id: str, claim: str) -> bool:
    with get_conn() as conn:
        return bool(
            conn.execute(
                'SELECT 1 FROM auto_topups WHERE organization_id = %s AND charging_since = %s', (org_id, claim)
            ).fetchone()
        )


def _fail(org_id: str, claim: str, error: str) -> None:
    with get_conn() as conn:
        turned_off = conn.execute(
            """UPDATE auto_topups SET enabled = false, charging_since = NULL, last_error = %s, updated_at = now()
               WHERE organization_id = %s AND charging_since = %s""",
            (error[:200], org_id, claim),
        ).rowcount
    logger.warning('billing.auto_topup.failed', org_id=org_id, error=error[:200])
    if not turned_off:
        return
    try:  # after the commit: a notification failure must never leave auto top-up on and claimed
        with get_conn() as conn:
            admins = conn.execute(
                f'SELECT "userId"::text AS id FROM {balance.ORG_ADMINS_SQL.format(org="%s")} a', (org_id,)
            ).fetchall()
            # every admin of the organization (they manage the card); cannot be turned off
            data = {'organization': notifications.org_name(conn, org_id)}
            notified = [
                notifications.notify(
                    conn, a['id'], 'payment_failed', href=billing_href(org_id), data=data, org_id=org_id
                )
                for a in admins
            ]
        for n in notified:
            notifications.deliver(n)
    except Exception as exc:
        logger.error('billing.auto_topup.notify_failed', org_id=org_id, error_type=type(exc).__name__)


def _discard(invoice_id: str, status: str) -> None:
    """Leave no unpaid invoice behind a failed charge (best effort)."""
    key = settings.stripe_secret_key
    try:
        if status == 'draft':
            stripe.Invoice.delete(invoice_id, api_key=key)
        elif status == 'open':
            stripe.Invoice.void_invoice(invoice_id, api_key=key)
    except stripe.StripeError as e:
        logger.error('billing.auto_topup.discard_failed', invoice_id=invoice_id, error=str(e)[:200])


def charge(org_id: str, pack_id: str, claim: str) -> str:
    """One off-session top-up for a claimed organization. Returns 'paid' | 'pending' | 'failed' | 'skipped'."""
    if not _holds_claim(org_id, claim):
        return 'skipped'  # a redelivered task whose top-up was already credited (or failed)
    pack = next((p for p in settings.billing_packs if p.id == pack_id and p.stripe_price_id), None)
    with get_conn() as conn:
        row = conn.execute(
            """SELECT b.stripe_customer_id, a.user_id::text FROM billing_customers b
               JOIN auto_topups a ON a.organization_id = b.organization_id WHERE b.organization_id = %s""",
            (org_id,),
        ).fetchone()
    if pack is None or row is None:
        _fail(org_id, claim, 'pack_unavailable' if pack is None else 'no_payment_method')
        return 'failed'
    key, customer = settings.stripe_secret_key, row['stripe_customer_id']
    idem = f'zenbree-auto-topup-{org_id}-{claim}'  # a retried task reuses the same invoice
    inv = None
    try:
        pm = payment_method(customer)
        if pm is None:
            _fail(org_id, claim, 'no_payment_method')
            return 'failed'
        inv = stripe.Invoice.create(
            api_key=key,
            customer=customer,
            collection_method='charge_automatically',
            auto_advance=False,
            automatic_tax={'enabled': True},
            default_payment_method=pm,
            pending_invoice_items_behavior='exclude',
            description='Auto top-up',
            # user_id: who switched auto top-up on, recorded as who bought
            metadata={
                'kind': KIND,
                'organization_id': org_id,
                'user_id': row['user_id'] or '',  # '' once that member deleted their account
                'pack_id': pack.id,
                'credits': str(pack.credits),
            },
            idempotency_key=f'{idem}-invoice',
        )
        # A redelivered task gets the first run's invoice back from the idempotency key, as it was
        # then: its current state says which steps are left.
        inv = stripe.Invoice.retrieve(inv.id, api_key=key)
        if inv.status == 'draft':
            stripe.InvoiceItem.create(
                api_key=key,
                customer=customer,
                invoice=inv.id,
                pricing={'price': pack.stripe_price_id},
                idempotency_key=f'{idem}-item',
            )
            inv = stripe.Invoice.finalize_invoice(inv.id, api_key=key)
        if inv.status == 'open':
            inv = stripe.Invoice.pay(inv.id, api_key=key, off_session=True)
    except Exception as e:
        # Declined, authentication required, Stripe slow or down (soft time limit), DB error: off until the
        # user saves again. A paid invoice is never discarded here, and invoice.paid still credits it.
        if inv is not None and inv.status != 'paid':
            _discard(inv.id, inv.status or '')
        _fail(org_id, claim, getattr(e, 'code', None) or type(e).__name__)
        return 'failed'
    if inv.status != 'paid':
        return 'pending'  # still processing: invoice.paid credits it and releases the claim
    with get_conn() as conn:
        credit_invoice(conn, inv.to_dict())
    balance.changed(org_id)
    return 'paid'
