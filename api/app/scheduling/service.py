from datetime import UTC, datetime, timedelta
from functools import partial

from psycopg.types.json import Jsonb

from app.core import scope as scopes
from app.core import tasks
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.scheduling.models import SearchInput


def _in_scope_org(column: str) -> tuple[str, tuple[str, ...]]:
    """`` AND <column> = <the scope's organization>`` and its parameter. Row-level security already holds these reads
    to that organization; the term keeps them right without it. With no scope it raises ``ScopeMissing``, as a
    checkout does. A ``System`` scope bypasses row-level security, so it is refused too: no beat reads saved searches
    through these functions."""
    scope = scopes.current()
    if isinstance(scope, scopes.System):
        raise scopes.ScopeMissing(f'a saved search belongs to one organization: enter its scope, not {scope}')
    return f' AND {column} = %s', (scope.org,)


def owned(conn, user_id, search_id, *, lock=False, admin=False, manage=True):
    """The saved search, shared like its list (#323): any member of the organization sees it (row-level security
    holds the read to the scope's organization), its creator or an admin manages it (``manage``: else 403)."""
    in_org, org = _in_scope_org('organization_id')
    row = conn.execute(
        'SELECT * FROM saved_searches WHERE id = %s AND deleted_at IS NULL' + in_org + (' FOR UPDATE' if lock else ''),
        (search_id, *org),
    ).fetchone()
    if not row:
        raise api_error(404, 'SAVED_SEARCH.NOT_FOUND', 'saved search not found')
    if manage and str(row['user_id']) != str(user_id) and not admin:
        raise api_error(403, 'SAVED_SEARCH.NOT_OWNER', 'Only who saved the search, or an admin')
    return row


def validate_links(conn, user_id, data):
    """The search's list and campaign are the user's, in an organization they still belong to (#306/#308: a run
    spends that organization's credits and writes to its list). The scope holds the list to its organization."""
    entity = 'person' if data['tool_name'] == 'search_people' else 'company'
    in_org, org = _in_scope_org('l.organization_id')
    row = conn.execute(
        'SELECT l.organization_id FROM lists l WHERE l.id = %s AND l.user_id = %s AND l.entity = %s AND l.deleted_at IS NULL'
        + in_org
        + ' AND EXISTS (SELECT 1 FROM member m WHERE m."userId" = l.user_id AND m."organizationId" = l.organization_id)'
        ' FOR SHARE',
        (data['list_id'], user_id, entity, *org),
    ).fetchone()
    if not row:
        raise api_error(404, 'LIST.NOT_FOUND', 'matching list not found')
    if (
        data.get('campaign_id')
        and not conn.execute(
            'SELECT id FROM outreach_campaigns WHERE id = %s AND owner_id = %s AND organization_id = %s FOR SHARE',
            (data['campaign_id'], user_id, row['organization_id']),
        ).fetchone()
    ):
        raise api_error(404, 'CAMPAIGN.NOT_FOUND', 'campaign not found')


def in_list_project(conn, list_id, campaign_id) -> bool:
    """The campaign is in the list's project. A project is a client (#307): a search never refills one client's
    campaign with another's people (#1086)."""
    return bool(
        conn.execute(
            'SELECT 1 FROM lists l JOIN outreach_campaigns c ON c.project_id = l.project_id WHERE l.id = %s AND c.id = %s',
            (list_id, campaign_id),
        ).fetchone()
    )


def save(user_id, body: SearchInput, search_id=None, admin=False):
    data = body.model_dump(mode='json')
    with get_conn() as conn:
        # Serialises per-user schedule limits and concurrent creates.
        conn.execute('SELECT id FROM "user" WHERE id = %s FOR NO KEY UPDATE', (user_id,)).fetchone()
        if search_id:
            # An admin edits it for its creator: runs keep spending as them, from their list and campaign.
            user_id = owned(conn, user_id, search_id, lock=True, admin=admin)['user_id']
            if conn.execute(
                "SELECT id FROM saved_search_runs WHERE saved_search_id = %s AND status IN ('queued','running')",
                (search_id,),
            ).fetchone():
                raise api_error(
                    409, 'SAVED_SEARCH.RUN_IN_PROGRESS', 'Pause and wait for the current run before editing'
                )
        elif (
            conn.execute(
                'SELECT count(*) AS n FROM saved_searches WHERE user_id = %s AND deleted_at IS NULL', (user_id,)
            ).fetchone()['n']
            >= 10
        ):
            raise api_error(409, 'SAVED_SEARCH.CAP_REACHED', 'At most 10 saved searches')
        validate_links(conn, user_id, data)
        if data.get('campaign_id') and not in_list_project(conn, data['list_id'], data['campaign_id']):
            raise api_error(404, 'CAMPAIGN.NOT_FOUND', 'campaign not found')
        values = [data[k] for k in ('name', 'tool_name')]
        values += [Jsonb(data['tool_input'])]
        values += [data[k] for k in ('list_id', 'campaign_id', 'cadence', 'next_run_at', 'enabled')]
        if search_id:
            return conn.execute(
                'UPDATE saved_searches SET name=%s, tool_name=%s, tool_input=%s, list_id=%s,'
                ' campaign_id=%s, cadence=%s, next_run_at=%s, enabled=%s, updated_at=now()'
                ' WHERE id=%s AND user_id=%s RETURNING *',
                (*values, search_id, user_id),
            ).fetchone()
        return conn.execute(
            'INSERT INTO saved_searches (name,tool_name,tool_input,list_id,campaign_id,cadence,'
            'next_run_at,enabled,user_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
            (*values, user_id),
        ).fetchone()


def list_searches():
    """Every member's saved searches on the scope organization's lists (#323)."""
    in_org, org = _in_scope_org('s.organization_id')
    with get_conn() as conn:
        return conn.execute(
            'SELECT s.* FROM saved_searches s JOIN lists l ON l.id = s.list_id'
            f' WHERE s.deleted_at IS NULL AND l.deleted_at IS NULL{in_org} ORDER BY s.created_at DESC',
            org,
        ).fetchall()


def toggle(user_id, search_id, *, enabled=False, delete=False, admin=False):
    with get_conn() as conn:
        owned(conn, user_id, search_id, lock=True, admin=admin)
        row = conn.execute(
            'UPDATE saved_searches SET enabled=%s, deleted_at=CASE WHEN %s THEN now() ELSE NULL END,'
            ' updated_at=now() WHERE id=%s RETURNING *',
            (enabled, delete, search_id),
        ).fetchone()
        if not enabled:
            conn.execute(
                "UPDATE saved_search_runs SET status='cancelled', finished_at=now(), error_code='schedule_paused'"
                " WHERE saved_search_id=%s AND status='queued'",
                (search_id,),
            )
        return row


def enqueue(conn, search, when):
    # Caller locks the search: manual and scheduled runs share the same claim.
    existing = conn.execute(
        "SELECT * FROM saved_search_runs WHERE saved_search_id=%s AND status IN ('queued','running')", (search['id'],)
    ).fetchone()
    if existing:
        return existing
    config = SearchInput.model_validate({k: search[k] for k in SearchInput.model_fields}).model_dump(mode='json')
    return conn.execute(
        'INSERT INTO saved_search_runs (saved_search_id,user_id,scheduled_for,config) VALUES (%s,%s,%s,%s)'
        ' ON CONFLICT (saved_search_id,scheduled_for) DO NOTHING RETURNING *',
        (search['id'], search['user_id'], when, Jsonb(config)),
    ).fetchone()


def run_now(user_id, search_id, admin=False):
    with get_conn() as conn:
        search = owned(conn, user_id, search_id, lock=True, admin=admin)
        if not search['enabled']:
            raise api_error(409, 'SAVED_SEARCH.PAUSED', 'Saved search is paused')
        links = {k: search[k] for k in ('tool_name', 'list_id', 'campaign_id')}
        validate_links(conn, search['user_id'], links)  # the run spends as its creator
        return enqueue(conn, search, datetime.now(UTC))


# Due, on a live list. The selection also wants an owner who is a live member of the search's organization: a
# deleted (#333), removed or banned (#389) owner's searches wait, and a return or an unban resumes them.
_DUE_SQL = (
    'enabled AND deleted_at IS NULL AND next_run_at <= now()'
    # #341: a deleted list's searches wait; a restore resumes them.
    ' AND NOT EXISTS (SELECT 1 FROM lists l WHERE l.id = list_id AND l.deleted_at IS NOT NULL)'
)


def schedule_due() -> list[tuple[str, str, str]]:
    """Queue a run of every due search, as its owner, and return ``(organization, owner, run id)`` of every queued
    run. Called in the beat's System scope, which only selects: each search is scheduled as its owner."""
    with get_conn() as conn:
        due = conn.execute(
            f'SELECT id, organization_id, user_id FROM saved_searches WHERE {_DUE_SQL}'
            ' AND (user_id, organization_id) IN (SELECT user_id, organization_id FROM zb.live_members)'
            ' ORDER BY next_run_at LIMIT 100'
        ).fetchall()
    for search in due:
        tasks.for_member(str(search['organization_id']), str(search['user_id']), partial(_schedule, search['id']))
    with get_conn() as conn:
        queued = conn.execute(
            "SELECT id, organization_id, user_id FROM saved_search_runs WHERE status='queued' AND user_id IS NOT NULL"
            ' ORDER BY created_at LIMIT 100'
        ).fetchall()
    return [(str(r['organization_id']), str(r['user_id']), str(r['id'])) for r in queued]


def _schedule(search_id) -> None:
    with get_conn() as conn:
        search = conn.execute(
            f'SELECT * FROM saved_searches WHERE id = %s AND {_DUE_SQL} FOR UPDATE SKIP LOCKED', (search_id,)
        ).fetchone()
        if search is None:  # another beat has it, or it changed since the selection
            return
        enqueue(conn, search, search['next_run_at'])
        days = 1 if search['cadence'] == 'daily' else 7
        next_run = search['next_run_at']
        now = datetime.now(UTC)
        next_run += timedelta(days=days * (int((now - next_run).total_seconds() // (days * 86400)) + 1))
        conn.execute('UPDATE saved_searches SET next_run_at=%s WHERE id=%s', (next_run, search['id']))
