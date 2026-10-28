"""Backoffice API. Every route is admin-only (Better Auth role claim).

chat_sessions / chat_messages / jobs are created by feature/m1-agent
(schema_agent.sql); these reads go against existing columns.
"""

import re
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.admin import settings as kill_switches
from app.admin.costs import costs
from app.auth.account_delete import BeforeDelete, before_delete
from app.auth.middleware import CurrentUser, require_admin
from app.billing import balance
from app.billing.routes import Transaction
from app.billing.subscription import LIVE_STATUSES, Subscription, _live_subscription, latest_subscription
from app.core.db import get_conn
from app.core.errors_http import api_error

router = APIRouter(prefix='/v1/admin', tags=['admin'], dependencies=[Depends(require_admin)])


def _window(from_: date | None, to: date | None) -> tuple[date, date]:
    to = to or datetime.now(UTC).date()
    return from_ or to - timedelta(days=29), to


def _not_found(what: str) -> HTTPException:
    return api_error(404, 'ADMIN.NOT_FOUND', f'{what} not found')


class CostTotals(BaseModel):
    llm_cost_usd: float
    vendor_cost_usd: float
    credits_spent: int
    credits_purchased: int
    revenue_eur_cents: int
    cost_usd: float
    revenue_eur: float
    margin_eur: float


class UserCosts(CostTotals):
    user_id: str | None
    email: str | None


class DailyCosts(BaseModel):
    day: date
    llm_cost_usd: float
    vendor_cost_usd: float
    credits_spent: int
    revenue_eur_cents: int
    cost_usd: float
    revenue_eur: float
    margin_eur: float


class VendorCosts(BaseModel):
    vendor: str
    calls: int
    units: int
    cost_usd: float


class ProviderHits(BaseModel):
    provider: str
    channel: Literal['email', 'phone']
    lookups: int
    hits: int


class Costs(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: date = Field(alias='from')
    to: date
    eur_per_usd: float
    totals: CostTotals
    users: list[UserCosts]
    daily: list[DailyCosts]
    by_vendor: list[VendorCosts]
    by_provider: list[ProviderHits]


@router.get('/costs', response_model=Costs)
def get_costs(from_: Annotated[date | None, Query(alias='from')] = None, to: date | None = None) -> dict:
    return costs(*_window(from_, to))


# ── Users: credits and usage ───────────────────────────────────────────────


class CreditsIn(BaseModel):
    # Inside the credit functions' INT amount, so an oversized one is a 422, not a 500 (#393).
    amount: int = Field(..., ge=-1_000_000_000, le=1_000_000_000, description='>0 grants, <0 deducts')
    reason: str = Field(..., min_length=1, max_length=500)


def _adjust(org, user_id, body: CreditsIn, admin: CurrentUser) -> int:
    """Grant or deduct on an organization's wallet. ``user_id``: whose account it lands on. Who did it:
    reference_type='admin', reference_id=<admin id>."""
    if body.amount == 0:
        raise api_error(422, 'ADMIN.ZERO_AMOUNT', 'amount must be non-zero')
    if body.amount > 0:
        sql = "SELECT increment_credits(%s, %s, %s, 'adjustment', 'admin', %s, NULL, NULL, %s) AS b"
    else:
        sql = "SELECT deduct_credits(%s, %s, %s, 'admin', %s, NULL, NULL, %s) AS b"
    with get_conn() as conn:
        new_balance = conn.execute(sql, (org, user_id, abs(body.amount), admin.id, body.reason)).fetchone()['b']
    if new_balance == -1:
        raise api_error(409, 'ADMIN.INSUFFICIENT_CREDITS', 'Balance lower than amount')
    balance.changed(org)
    return new_balance


@router.post('/users/{user_id}/before-delete', response_model=BeforeDelete)
async def before_user_delete(user_id: UUID) -> dict:
    """Before an admin deletes an account (#333, #341; the web calls it from Better Auth's user delete hook)."""
    return await before_delete(user_id)


# The company of a user's business profile: the one of their own organization's default project (its oldest,
# as `default_project`), never a client project's (#307) or another organization's.
_COMPANY = """(SELECT bp.profile->>'company_name' FROM business_profiles bp
               WHERE bp.project_id = (SELECT p.id FROM projects p WHERE p.organization_id = own_organization(u.id)
                                      ORDER BY p.created_at LIMIT 1)
               ORDER BY bp.updated_at DESC LIMIT 1)"""


class UserBalance(BaseModel):
    user_id: UUID
    balance: int


@router.post('/users/{user_id}/credits', response_model=UserBalance)
def adjust_credits(user_id: UUID, body: CreditsIn, admin: CurrentUser = Depends(require_admin)) -> dict:
    with get_conn() as conn:
        if not conn.execute('SELECT 1 FROM "user" WHERE id = %s::uuid', (user_id,)).fetchone():
            raise _not_found('User')
        # The user's own organization's wallet (#306).
        org = conn.execute('SELECT own_organization(%s::uuid) AS id', (user_id,)).fetchone()['id']
    return {'user_id': user_id, 'balance': _adjust(org, user_id, body, admin)}


class LedgerEntry(Transaction):
    reference_id: str | None


class OrganizationLedgerEntry(LedgerEntry):
    email: str | None


class Purchase(BaseModel):
    id: UUID
    pack_id: str
    credits: int
    amount_cents: int
    currency: str
    refunded_cents: int
    stripe_session_id: str | None
    created_at: datetime


class UserOrganization(BaseModel):
    id: UUID
    name: str
    role: str


class UsageCosts(BaseModel):
    totals: CostTotals
    daily: list[DailyCosts]
    by_vendor: list[VendorCosts]


class UserUsage(BaseModel):
    user_id: str
    email: str
    balance: int | None
    company: str | None
    subscription: Subscription | None
    organizations: list[UserOrganization]
    ledger: list[LedgerEntry]
    ledger_total: int
    purchases: list[Purchase]
    eur_per_usd: float
    costs: UsageCosts


@router.get('/users/{user_id}/usage', response_model=UserUsage)
def user_usage(
    user_id: UUID, from_: Annotated[date | None, Query(alias='from')] = None, to: date | None = None
) -> dict:
    with get_conn() as conn:
        # The wallet of the org the user founded (slug = their id, #306), not of a team they created (#676)
        user = conn.execute(
            f"""SELECT u.id::text AS user_id, u.email, c.credits_remaining AS balance,
                      own_organization(u.id)::text AS org_id, {_COMPANY} AS company
               FROM "user" u LEFT JOIN organization o ON o.slug = u.id::text
               LEFT JOIN user_credits c ON c.organization_id = o.id WHERE u.id = %s::uuid""",
            (user_id,),
        ).fetchone()
        if not user:
            raise _not_found('User')
        ledger = conn.execute(
            """SELECT id, type, status, amount, balance_after, reference_type, reference_id,
                      description, tool_name, created_at
               FROM credit_transactions WHERE user_id = %s ORDER BY created_at DESC LIMIT 100""",
            (user_id,),
        ).fetchall()
        ledger_total = conn.execute(
            'SELECT count(*)::int AS n FROM credit_transactions WHERE user_id = %s', (user_id,)
        ).fetchone()['n']
        purchases = conn.execute(
            """SELECT id, pack_id, credits, amount_cents, currency, refunded_cents, stripe_session_id, created_at
               FROM purchases WHERE user_id = %s ORDER BY created_at DESC""",
            (user_id,),
        ).fetchall()
        organizations = conn.execute(
            """SELECT o.id, o.name, m.role FROM member m JOIN organization o ON o.id = m."organizationId"
               WHERE m."userId" = %s::uuid ORDER BY o.slug = %s::text DESC, m."createdAt" """,
            (user_id, str(user_id)),
        ).fetchall()
    c = costs(*_window(from_, to), user_id=str(user_id))
    org_id = user.pop('org_id')
    return {
        **user,
        # The plan of their own organization's wallet (#306), with its renewal date (#352).
        'subscription': _live_subscription(org_id) if org_id else None,
        'organizations': organizations,
        'ledger': ledger,
        'ledger_total': ledger_total,
        'purchases': purchases,
        'eur_per_usd': c['eur_per_usd'],
        'costs': {'totals': c['totals'], 'daily': c['daily'], 'by_vendor': c['by_vendor']},
    }


class UserOverview(BaseModel):
    user_id: str
    balance: int | None
    company: str | None
    plan_id: str | None
    has_purchases: bool
    last_active_at: datetime | None


class UsersOverview(BaseModel):
    users: list[UserOverview]


@router.get('/users/overview', response_model=UsersOverview)
def users_overview(ids: Annotated[list[UUID], Query(max_length=200)]) -> dict:
    """What the users list shows next to Better Auth's own columns, for the ids on one page."""
    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT u.id::text AS user_id, c.credits_remaining AS balance,
                      {_COMPANY} AS company,
                      (SELECT s.plan_id FROM subscriptions s WHERE s.organization_id = o.id AND s.status = ANY(%(live)s)
                       ORDER BY s.updated_at DESC LIMIT 1) AS plan_id,
                      EXISTS (SELECT 1 FROM purchases p WHERE p.organization_id = o.id) AS has_purchases,
                      (SELECT max(se."updatedAt") FROM session se
                       WHERE se."userId" = u.id AND se."impersonatedBy" IS NULL) AS last_active_at
               FROM "user" u
               LEFT JOIN organization o ON o.slug = u.id::text LEFT JOIN user_credits c ON c.organization_id = o.id
               WHERE u.id = ANY(%(ids)s::uuid[])""",
            {'ids': ids, 'live': list(LIVE_STATUSES)},
        ).fetchall()
    return {'users': rows}


# ── Organizations: the wallet, its members and clients (#327) ──────────────


class OrganizationSummary(BaseModel):
    id: UUID
    name: str
    slug: str
    created_at: datetime
    balance: int | None
    members: int
    plan_id: str | None
    has_purchases: bool


class OrganizationPage(BaseModel):
    items: list[OrganizationSummary]
    total: int


@router.get('/organizations', response_model=OrganizationPage)
def organizations(
    q: Annotated[str, Query(max_length=200)] = '',
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    """Newest first. ``q`` matches the name, the slug or a member's email, literally (``%`` and ``_`` too)."""
    term = q.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    args = {'q': f'%{term}%' if term else None, 'live': list(LIVE_STATUSES), 'lim': limit, 'off': offset}
    where = """%(q)s::text IS NULL OR o.name ILIKE %(q)s OR o.slug ILIKE %(q)s
               OR EXISTS (SELECT 1 FROM member m JOIN "user" u ON u.id = m."userId"
                          WHERE m."organizationId" = o.id AND u.email ILIKE %(q)s)"""
    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT o.id, o.name, o.slug, o."createdAt" AS created_at, c.credits_remaining AS balance,
                       (SELECT count(*) FROM member m WHERE m."organizationId" = o.id)::int AS members,
                       (SELECT s.plan_id FROM subscriptions s
                        WHERE s.organization_id = o.id AND s.status = ANY(%(live)s)
                        ORDER BY s.updated_at DESC LIMIT 1) AS plan_id,
                       EXISTS (SELECT 1 FROM purchases p WHERE p.organization_id = o.id) AS has_purchases
                FROM organization o LEFT JOIN user_credits c ON c.organization_id = o.id
                WHERE {where} ORDER BY o."createdAt" DESC, o.id LIMIT %(lim)s OFFSET %(off)s""",
            args,
        ).fetchall()
        total = conn.execute(f'SELECT count(*)::int AS n FROM organization o WHERE {where}', args).fetchone()['n']
    return {'items': rows, 'total': total}


class OrganizationMember(BaseModel):
    user_id: UUID
    email: str
    name: str
    role: str
    created_at: datetime


class OrganizationProject(BaseModel):
    id: UUID
    name: str
    created_at: datetime
    credit_cap: int | None
    credit_cap_mode: Literal['hard', 'soft'] | None
    credits_this_month: int


class OrganizationDetail(BaseModel):
    id: UUID
    name: str
    slug: str
    created_at: datetime
    balance: int | None
    members: list[OrganizationMember]
    subscription: Subscription | None
    ledger: list[OrganizationLedgerEntry]
    ledger_total: int
    purchases: list[Purchase]
    projects: list[OrganizationProject]


@router.get('/organizations/{org_id}', response_model=OrganizationDetail)
def organization(org_id: UUID) -> dict:
    with get_conn() as conn:
        org = conn.execute(
            """SELECT o.id, o.name, o.slug, o."createdAt" AS created_at, c.credits_remaining AS balance
               FROM organization o LEFT JOIN user_credits c ON c.organization_id = o.id WHERE o.id = %s""",
            (org_id,),
        ).fetchone()
        if not org:
            raise _not_found('Organization')
        members = conn.execute(
            """SELECT u.id AS user_id, u.email, u.name, m.role, m."createdAt" AS created_at
               FROM member m JOIN "user" u ON u.id = m."userId" WHERE m."organizationId" = %s
               ORDER BY m.role = 'admin' DESC, m."createdAt" """,
            (org_id,),
        ).fetchall()
        subscription = latest_subscription(conn, org_id, live_only=False)
        ledger = conn.execute(
            """SELECT t.id, t.type, t.status, t.amount, t.balance_after, t.reference_type, t.reference_id,
                      t.description, t.tool_name, t.created_at, u.email
               FROM credit_transactions t LEFT JOIN "user" u ON u.id = t.user_id
               WHERE t.organization_id = %s ORDER BY t.created_at DESC LIMIT 100""",
            (org_id,),
        ).fetchall()
        ledger_total = conn.execute(
            'SELECT count(*)::int AS n FROM credit_transactions WHERE organization_id = %s', (org_id,)
        ).fetchone()['n']
        purchases = conn.execute(
            """SELECT id, pack_id, credits, amount_cents, currency, refunded_cents, stripe_session_id, created_at
               FROM purchases WHERE organization_id = %s ORDER BY created_at DESC""",
            (org_id,),
        ).fetchall()
        projects = conn.execute(
            """SELECT id, name, created_at, credit_cap, credit_cap_mode, project_month_spend(id) AS credits_this_month
               FROM projects WHERE organization_id = %s ORDER BY created_at""",
            (org_id,),
        ).fetchall()
    return {
        **org,
        'members': members,
        'subscription': subscription,
        'ledger': ledger,
        'ledger_total': ledger_total,
        'purchases': purchases,
        'projects': projects,
    }


class OrganizationBalance(BaseModel):
    organization_id: UUID
    balance: int


@router.post('/organizations/{org_id}/credits', response_model=OrganizationBalance)
def adjust_organization_credits(org_id: UUID, body: CreditsIn, admin: CurrentUser = Depends(require_admin)) -> dict:
    with get_conn() as conn:
        if not conn.execute('SELECT 1 FROM organization WHERE id = %s', (org_id,)).fetchone():
            raise _not_found('Organization')
        # Lands on its oldest admin's account, never the back-office admin's: the ledger names a member of it.
        row = conn.execute(
            """SELECT m."userId" AS id FROM member m WHERE m."organizationId" = %s
               ORDER BY m.role = 'admin' DESC, m."createdAt" LIMIT 1""",
            (org_id,),
        ).fetchone()
    if not row:
        raise api_error(409, 'ADMIN.ORG_HAS_NO_MEMBERS', 'The organization has no member to credit')
    return {'organization_id': org_id, 'balance': _adjust(org_id, row['id'], body, admin)}


class Badges(BaseModel):
    jobs_attention: int
    switches_on: int


@router.get('/badges', response_model=Badges)
def badges() -> dict:
    """The back office rail's counters: jobs that failed or wait on input in the last day, kill switches on."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT (SELECT count(*) FROM jobs WHERE status IN ('failed', 'needs_input')
                          AND created_at > now() - interval '24 hours')::int AS jobs_attention,
                      (SELECT count(*) FROM settings WHERE value = 'true'::jsonb)::int AS switches_on"""
        ).fetchone()


# ── Settings / kill switches ───────────────────────────────────────────────


class Setting(BaseModel):
    key: str
    value: Any
    updated_at: datetime
    updated_by: UUID | None
    updated_by_name: str | None


class VendorErrors(BaseModel):
    vendor: str
    errors: int
    last_at: datetime


class Settings(BaseModel):
    settings: list[Setting]
    vendor_errors: list[VendorErrors]


@router.get('/settings', response_model=Settings)
def get_settings() -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT s.key, s.value, s.updated_at, s.updated_by, u.name AS updated_by_name
               FROM settings s LEFT JOIN "user" u ON u.id = s.updated_by ORDER BY s.key"""
        ).fetchall()
        # Failed vendor calls in the last day, so a flaky vendor stands out next to its kill switch (#352).
        # ponytail: no index on created_at alone, so this scans vendor_calls; a partial one on failures if it grows.
        errors = conn.execute(
            """SELECT vendor, count(*)::int AS errors, max(created_at) AS last_at FROM vendor_calls
               WHERE NOT success AND created_at > now() - interval '24 hours'
               GROUP BY vendor ORDER BY errors DESC, vendor"""
        ).fetchall()
    return {'settings': rows, 'vendor_errors': errors}


class SettingIn(BaseModel):
    key: str = Field(..., min_length=1, max_length=200)
    value: Any = None  # null deletes the key


@router.put('/settings', response_model=Settings)
def put_setting(body: SettingIn, admin: CurrentUser = Depends(require_admin)) -> dict:
    with get_conn() as conn:
        if body.value is None:
            conn.execute('DELETE FROM settings WHERE key = %s', (body.key,))
        else:
            conn.execute(
                """INSERT INTO settings (key, value, updated_by) VALUES (%s, %s, %s)
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now(),
                                                   updated_by = EXCLUDED.updated_by""",
                (body.key, Jsonb(body.value), admin.id),
            )
    kill_switches.invalidate()  # this process only; others within the 30s TTL
    return get_settings()


class NewSettingIn(BaseModel):
    key: str = Field(..., max_length=200)


# A typo'd key would block nothing, and re-adding an existing one would reset it to false (#338).
@router.post('/settings', response_model=Settings)
def add_setting(body: NewSettingIn, admin: CurrentUser = Depends(require_admin)) -> dict:
    if not re.fullmatch(r'(vendor|channel)_disabled:[a-z0-9_-]+', body.key):
        raise api_error(
            422, 'ADMIN.INVALID_SETTING_KEY', 'key must be vendor_disabled:<name> or channel_disabled:<name>'
        )
    with get_conn() as conn:
        added = conn.execute(
            """INSERT INTO settings (key, value, updated_by) VALUES (%s, 'false'::jsonb, %s)
               ON CONFLICT (key) DO NOTHING RETURNING key""",
            (body.key, admin.id),
        ).fetchone()
    if not added:
        raise api_error(409, 'ADMIN.SETTING_EXISTS', f'{body.key} already exists')
    return get_settings()
