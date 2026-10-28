"""The Stripe customer's email follows the login email of the admin who created it (#499, #965).

Stripe sends invoices and receipts to the customer's email, set once when the customer is created. A user
can change their login email afterwards (Better Auth ``changeEmail``, #497): the change is confirmed from a
link, in any browser, and an admin can rewrite it from the back office, so no request of ours is sure to see
it happen. A beat sweep compares instead: ``billing_customers.email`` is the address Stripe was last given.
"""

import stripe
import structlog

from app.core.config import settings
from app.core.db import get_conn
from app.core.scope import acting_for

logger = structlog.get_logger()

# ponytail: one batch a sweep, oldest customers first. A customer Stripe keeps refusing stays at the head and
# is retried every sweep; with more than BATCH of those the rest would wait. Email changes are rare.
BATCH = 50

# The login email is followed only while its owner is still a live admin of the organization, and never
# once the organization saved a billing profile: that email is the admins' own choice (#965). A customer
# whose creator was deleted, removed or demoted keeps the address it has.
_STALE_SQL = """SELECT b.organization_id, b.stripe_customer_id, u.email
                FROM billing_customers b JOIN "user" u ON u.id = b.user_id
                JOIN member m ON m."organizationId" = b.organization_id AND m."userId" = b.user_id
                    AND m.role = 'admin'
                JOIN zb.live_members l ON l.organization_id = b.organization_id AND l.user_id = b.user_id
                WHERE coalesce(btrim(u.email), '') <> '' AND u.email IS DISTINCT FROM b.email
                    AND NOT EXISTS (SELECT 1 FROM billing_profiles p WHERE p.organization_id = b.organization_id)
                ORDER BY b.created_at LIMIT %s"""


def sync() -> dict:
    """Give Stripe the current address of every customer whose creator's email changed. Safe to repeat: the
    column moves only after Stripe accepted the address, and a second run finds nothing to send."""
    if not settings.stripe_secret_key:
        return {'updated': 0, 'failed': 0}
    with get_conn() as conn:
        stale = conn.execute(_STALE_SQL, (BATCH,)).fetchall()
    updated = failed = 0
    for row in stale:
        try:
            stripe.Customer.modify(row['stripe_customer_id'], api_key=settings.stripe_secret_key, email=row['email'])
        except stripe.StripeError as exc:
            logger.warning(
                'billing.customer_email.sync_failed',
                organization_id=str(row['organization_id']),
                error_type=type(exc).__name__,
            )
            failed += 1
            continue
        # the beat crosses organizations to select; each customer is written as its organization
        with acting_for(str(row['organization_id']), 'billing email sync'), get_conn() as conn:
            conn.execute(
                'UPDATE billing_customers SET email = %s WHERE organization_id = %s',
                (row['email'], row['organization_id']),
            )
        updated += 1
    if updated or failed:
        logger.info('billing.customer_email.synced', updated=updated, failed=failed)
    return {'updated': updated, 'failed': failed}
