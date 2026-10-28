"""What runs before an account is deleted, by an admin or by the user (#333, #341). The web calls it from Better
Auth's delete hooks and aborts the delete on any error, so the two rules below hold on both paths:

- the last admin of an organization that has other members is never deleted (409 ``ACCOUNT.LAST_ADMIN``);
- an organization the user is the only member of stops billing first: its live Stripe subscriptions are cancelled
  and its auto top-up switched off. A Stripe failure is 502 ``ACCOUNT.BILLING_CANCEL_FAILED`` and nothing is deleted.
  Cancelling comes before the delete, so a delete that then fails leaves a user without a plan (they resubscribe),
  never a deleted user's organization still billing.

Then the senders they connected are released (``release_senders_of``).
"""

import asyncio
from uuid import UUID

import stripe
import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.middleware import CurrentUser, current_user
from app.auth.photo import remove_photo
from app.billing.subscription import LIVE_STATUSES
from app.core.config import settings
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.core.scope import system
from app.core.system_reasons import SystemReason
from app.integrations.senders import release_senders_of

router = APIRouter(prefix='/v1/me', tags=['account'])
logger = structlog.get_logger()

_LAST_ADMIN = """
SELECT o.name FROM member m JOIN organization o ON o.id = m."organizationId"
WHERE m."userId" = %(u)s AND m.role = 'admin'
  AND EXISTS (SELECT 1 FROM member x WHERE x."organizationId" = m."organizationId" AND x."userId" <> m."userId")
  AND NOT EXISTS (SELECT 1 FROM member x WHERE x."organizationId" = m."organizationId" AND x."userId" <> m."userId"
                  AND x.role = 'admin')
ORDER BY o.name
"""

_SOLE_MEMBER_ORGS = """
SELECT m."organizationId" AS id FROM member m WHERE m."userId" = %(u)s
  AND NOT EXISTS (SELECT 1 FROM member x WHERE x."organizationId" = m."organizationId" AND x."userId" <> m."userId")
"""


class BeforeDelete(BaseModel):
    released: int
    cancelled_subscriptions: int


def _plan(user_id: str) -> tuple[list[str], list[str], list[str]]:
    with get_conn() as conn:
        last_admin = [r['name'] for r in conn.execute(_LAST_ADMIN, {'u': user_id}).fetchall()]
        orgs = [str(r['id']) for r in conn.execute(_SOLE_MEMBER_ORGS, {'u': user_id}).fetchall()]
        subs = [
            r['stripe_subscription_id']
            for r in conn.execute(
                'SELECT stripe_subscription_id FROM subscriptions WHERE organization_id = ANY(%s::uuid[])'
                ' AND status = ANY(%s)',
                (orgs, list(LIVE_STATUSES)),
            ).fetchall()
        ]
    return last_admin, orgs, subs


_ENDED = ('canceled', 'incomplete_expired')


def _ended_at_stripe(sub_id: str, error: stripe.InvalidRequestError) -> bool:
    """A cancel Stripe refused because the subscription is already over (or gone): a local row the webhook never
    updated must not lock the user out of deleting."""
    if error.code == 'resource_missing':
        return True
    try:
        return stripe.Subscription.retrieve(sub_id, api_key=settings.stripe_secret_key).status in _ENDED
    except stripe.InvalidRequestError as missing:
        return missing.code == 'resource_missing'


def _cancel(sub_id: str) -> None:
    try:
        stripe.Subscription.cancel(sub_id, api_key=settings.stripe_secret_key)
    except stripe.InvalidRequestError as exc:
        if not _ended_at_stripe(sub_id, exc):
            raise
        logger.info('account.delete.already_ended', subscription_id=sub_id)
    # An immediate cancel always ends 'canceled'; the customer.subscription.deleted webhook writes the same later
    # (event_created is left as is, so it applies).
    with get_conn() as conn:
        conn.execute(
            "UPDATE subscriptions SET status = 'canceled', updated_at = now() WHERE stripe_subscription_id = %s",
            (sub_id,),
        )


def _stop_auto_topups(orgs: list[str]) -> None:
    with get_conn() as conn:
        conn.execute(
            'UPDATE auto_topups SET enabled = false, awaiting_card = false, updated_at = now()'
            ' WHERE organization_id = ANY(%s::uuid[])',
            (orgs,),
        )


async def before_delete(user_id: UUID | str) -> dict:
    uid = str(user_id)
    last_admin, orgs, subs = await asyncio.to_thread(_plan, uid)
    if last_admin:
        raise api_error(
            409,
            'ACCOUNT.LAST_ADMIN',
            f'The only admin of {", ".join(last_admin)}, which has other members: make another member admin first',
        )
    for sub_id in subs:
        try:
            await asyncio.to_thread(_cancel, sub_id)
        except stripe.StripeError as exc:
            logger.error('account.delete.cancel_failed', user_id=uid, error_type=type(exc).__name__)
            raise api_error(
                502, 'ACCOUNT.BILLING_CANCEL_FAILED', 'Could not cancel the subscription: nothing was deleted'
            ) from exc
    if orgs:
        await asyncio.to_thread(_stop_auto_topups, orgs)
    released = await release_senders_of(uid)
    await remove_photo(uid)
    logger.info('account.delete.prepared', user_id=uid, cancelled=len(subs), released=released)
    return {'released': released, 'cancelled_subscriptions': len(subs)}


class OwnDelete(BaseModel):
    user_id: UUID


@router.post('/before-delete', response_model=BeforeDelete)
async def before_own_delete(body: OwnDelete, user: CurrentUser = Depends(current_user)) -> dict:
    """The caller's own account, from Better Auth's deleteUser.beforeDelete while their session still mints a
    token (#341). ``user_id`` is the user Better Auth is deleting: a token for anyone else is 403."""
    if str(body.user_id) != user.id:
        raise api_error(403, 'ACCOUNT.NOT_YOURS', 'The token is not the account being deleted')
    # The account leaves every organization at once: its sole-member organizations' billing, its senders anywhere.
    with system(SystemReason.ACCOUNT_DELETE):
        return await before_delete(user.id)
