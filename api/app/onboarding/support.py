"""What the onboarding routes and the study stream share."""

from __future__ import annotations

from app.auth.middleware import CurrentUser
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.core.scope import acting_for
from app.llm.metering import MeteringContext, set_metering_context
from app.onboarding.own_domain import sync_own_domain


def save(project_id: str, user_id: str, **cols: object) -> None:
    """user_id: whoever last edited the project's profile."""
    names = ', '.join(cols)
    marks = ', '.join(['%s'] * len(cols))
    sets = ', '.join(f'{k} = EXCLUDED.{k}' for k in cols)
    with get_conn() as conn:
        conn.execute(
            f'INSERT INTO business_profiles (project_id, user_id, {names}) VALUES (%s, %s, {marks})'
            f' ON CONFLICT (project_id) DO UPDATE SET {sets}, user_id = EXCLUDED.user_id, updated_at = now()',
            (project_id, user_id, *cols.values()),
        )


def _saved_website(project_id: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute('SELECT website FROM business_profiles WHERE project_id = %s', (project_id,)).fetchone()
    return row['website'] if row else None


def save_study(project_id: str, user_id: str, website: str | None, description: str | None, profile: object) -> None:
    previous = _saved_website(project_id)
    save(project_id, user_id, website=website, description=description, profile=profile, segments=None)
    sync_own_domain(project_id, user_id, previous, website)


# Free LLM runs (study + segments) per organization, counted on its wallet (user_credits.onboarding_free_used, #306).
# Per organization, not per project (#307): a new client would otherwise be ten more free runs.
FREE_RUNS = 10

# The wallet that counts an organization's free onboarding runs: its founder's own organization's (#676). A team
# a user creates (slug `<their id>/<uuid>`, web/lib/team.ts) shares their runs, or each team would be ten more.
_FREE_RUN_WALLET = (
    'SELECT coalesce(f.id, o.id)::text AS id FROM organization o'
    " LEFT JOIN organization f ON f.slug = split_part(o.slug, '/', 1) WHERE o.id = %s"
)


def _wallet(org_id: str) -> str:
    """Read in the request's own scope: ``organization`` has no row-level security."""
    with get_conn() as conn:
        return conn.execute(_FREE_RUN_WALLET, (org_id,)).fetchone()['id']


# The wallet's row is in the founder's organization, which a teammate invited to the team is not a member of, so
# the write acts for that organization with no person: Member(wallet, caller) would refuse every teammate.
def use_free_run(org_id: str) -> None:
    wallet = _wallet(org_id)
    with acting_for(wallet, 'free onboarding run'), get_conn() as conn:
        ok = conn.execute(
            'UPDATE user_credits SET onboarding_free_used = onboarding_free_used + 1, updated_at = now()'
            ' WHERE organization_id = %s AND onboarding_free_used < %s RETURNING 1',
            (wallet, FREE_RUNS),
        ).fetchone()
    if not ok:
        raise api_error(429, 'ONBOARDING.LIMIT', 'Onboarding analysis limit reached')


def refund_free_run(org_id: str) -> None:
    wallet = _wallet(org_id)
    with acting_for(wallet, 'free onboarding run'), get_conn() as conn:
        conn.execute(
            'UPDATE user_credits SET onboarding_free_used = GREATEST(onboarding_free_used - 1, 0), updated_at = now()'
            ' WHERE organization_id = %s',
            (wallet,),
        )


def meter(user: CurrentUser) -> None:
    # Set in the task that calls the LLM: a ContextVar set in a thread or another task does not reach it.
    set_metering_context(MeteringContext(user_id=user.id, endpoint='onboarding'))
