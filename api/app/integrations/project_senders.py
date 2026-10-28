"""Which senders a project uses (#307). A sender (mailbox, LinkedIn account) is one physical row per account in an
organization, keyed by the provider's verified identity (``provider_account_id``: Google ``sub``, Microsoft ``id``,
the Unipile account id): one refresh token, one daily cap, one inbound subscription. A member may connect several
accounts of one provider. Projects use them through ``project_senders``: several mailboxes each (#935), and at most
one LinkedIn account. Any member sends from a project's senders; only whoever connected one, or an admin, replaces,
unlinks or disconnects it.
"""

from __future__ import annotations

import psycopg

from app.auth.middleware import CurrentUser
from app.core.db import get_conn
from app.core.errors import Result, err, is_err, ok
from app.integrations.mailbox_locks import lock_mailbox_twins
from app.outreach import sender_pool

_MAILBOXES = ('google', 'microsoft')
_LINKED_ROWS = (
    'SELECT u.* FROM project_senders ps JOIN user_oauth_connections u ON u.id = ps.sender_id'
    ' WHERE ps.project_id = %s AND ps.provider = ANY(%s)'
)
_LINKED = _LINKED_ROWS + ' ORDER BY u.provider, u.created_at, u.id'
_ADD = ' ON CONFLICT (project_id, sender_id) DO NOTHING'
# The predicate of the partial index project_senders_single_provider_key, repeated so Postgres infers the index.
_REPLACE = (
    " ON CONFLICT (project_id, provider) WHERE provider NOT IN ('google', 'microsoft')"
    ' DO UPDATE SET sender_id = EXCLUDED.sender_id, created_at = now()'
    ' WHERE project_senders.sender_id <> EXCLUDED.sender_id'
)
_LEGACY_FORBIDDEN = (
    "this client already sends from a colleague's mailbox with this address: only they or an admin can connect it"
)


def linked(conn, project_id, providers: tuple[str, ...] = ('google', 'microsoft')) -> list[dict]:
    """The project's senders of these providers, any status."""
    return conn.execute(_LINKED, (project_id, list(providers))).fetchall()


def linked_one(conn, project_id, provider: str) -> dict | None:
    rows = linked(conn, project_id, (provider,))
    return rows[0] if rows else None


def by_id(conn, project_id, sender_id, providers: tuple[str, ...] = ('google', 'microsoft')) -> dict | None:
    """The project's live sender with this id, or ``None``: an account the project does not use is not found."""
    return conn.execute(
        'SELECT u.* FROM project_senders ps JOIN user_oauth_connections u ON u.id = ps.sender_id'
        " WHERE ps.project_id = %s AND ps.provider = ANY(%s) AND u.id = %s AND u.status <> 'disconnected'",
        (project_id, list(providers), sender_id),
    ).fetchone()


def _link(conn, project_id, sender_id, provider: str, org) -> bool:
    """False when the client already used the account, whose pools then stay as picked."""
    return bool(
        conn.execute(
            'INSERT INTO project_senders (project_id, sender_id, provider, organization_id) VALUES (%s, %s, %s, %s)'
            + (_ADD if provider in _MAILBOXES else _REPLACE),
            (project_id, sender_id, provider, org),
        ).rowcount
    )


def _is_linked(conn, project_id, sender_id) -> bool:
    return bool(
        conn.execute(
            'SELECT 1 FROM project_senders WHERE project_id = %s AND sender_id = %s', (project_id, sender_id)
        ).fetchone()
    )


def _legacy_row(conn, project_id, provider: str, account_label: str | None) -> dict | None:
    if not (account_label or '').strip():
        return None
    return conn.execute(
        _LINKED_ROWS + ' AND u.provider_account_id IS NULL AND mailbox_address(u.account_label) = mailbox_address(%s)'
        ' ORDER BY u.id LIMIT 1 FOR UPDATE OF u',
        (project_id, [provider], account_label),
    ).fetchone()


def _twin_account(conn, project_id, provider: str, account_label: str | None) -> bool:
    """The client links a live identified account of this mailbox address. Not locked: ``link`` already holds its
    rows in id order, and a lock taken now could come out of that order."""
    return bool((account_label or '').strip()) and bool(
        conn.execute(
            _LINKED_ROWS + " AND u.provider_account_id IS NOT NULL AND u.status <> 'disconnected'"
            ' AND mailbox_address(u.account_label) = mailbox_address(%s) LIMIT 1',
            (project_id, [provider], account_label),
        ).fetchone()
    )


def _linked_now(conn, project_id, sender_id) -> None:
    """The client links ``sender_id`` now. The client's conversations on a legacy row of the same mailbox that it no
    longer links go on from it (#1069), whatever the person's status: an answer to a person who replied also needs an
    account the client links. The SQL ``move_legacy_pins`` holds the rule, which the migration's backfill also ran.
    Then the account joins each pool where one of its conversations is live."""
    conn.execute('SELECT FROM move_legacy_pins(%s, %s)', (project_id, sender_id))
    sender_pool.sync_linked(conn, project_id, sender_id)


def _supersede(conn, project_id, legacy_id, account_id) -> None:
    """The client sends from ``account_id`` where it sent from the legacy row of the same mailbox. The legacy pool rows
    follow the link by cascade; a client that already linked the account drops the legacy link and its pool rows."""
    if _is_linked(conn, project_id, account_id):
        conn.execute('DELETE FROM project_senders WHERE project_id = %s AND sender_id = %s', (project_id, legacy_id))
    else:
        conn.execute(
            'UPDATE project_senders SET sender_id = %s WHERE project_id = %s AND sender_id = %s',
            (account_id, project_id, legacy_id),
        )
    _linked_now(conn, project_id, account_id)


def _lock_in_id_order(conn, project_id, org, provider: str, *, sender_id=None, account=None, label=None) -> None:
    """Lock, in id order, each account row that a connect or link of this account goes on to lock: the account, a
    legacy row of its address that the client links, and the client's account of ``provider`` that it replaces. A
    member's release locks the member's rows in id order too, so the two wait instead of deadlocking (#1085)."""
    conn.execute(
        """SELECT 1 FROM user_oauth_connections u
           WHERE u.organization_id = %(org)s AND u.provider = %(provider)s
             AND (u.id = %(sender)s OR u.provider_account_id = %(account)s
                  OR u.id IN (SELECT ps.sender_id FROM project_senders ps WHERE ps.project_id = %(project)s)
                     AND (u.provider NOT IN ('google', 'microsoft')
                          OR u.provider_account_id IS NULL AND mailbox_address(%(label)s) <> ''
                             AND mailbox_address(u.account_label) = mailbox_address(%(label)s)))
           ORDER BY u.id FOR UPDATE""",
        {
            'org': org,
            'provider': provider,
            'sender': sender_id,
            'account': account,
            'project': project_id,
            'label': label,
        },
    )


def _replaced(conn, project_id, provider: str) -> dict | None:
    """The client's account that a new one of ``provider`` replaces, locked. A mailbox replaces none."""
    if provider in _MAILBOXES:
        return None
    return conn.execute(_LINKED + ' FOR UPDATE OF u', (project_id, [provider])).fetchone()


def in_org(project_id: str, org_id: str) -> bool:
    with get_conn() as conn:
        return bool(
            project_id
            and conn.execute(
                'SELECT 1 FROM projects WHERE id = %s AND organization_id = %s', (project_id, org_id)
            ).fetchone()
        )


def may_disconnect(row: dict | None, user: CurrentUser) -> bool:
    """Whoever connected a sender, or an admin of the organization, disconnects or replaces it."""
    return row is None or str(row['user_id']) == user.id or user.org_role == 'admin'


def _same_account(row: dict, account: str) -> bool:
    """The provider's stable account id: a mailbox's subject (Google ``sub``, Microsoft ``id``), a LinkedIn account's
    Unipile id. Never the address or a name, which another account can carry."""
    return bool(account) and row['provider_account_id'] == account


def may_unlink_all(conn, project_id, user: CurrentUser) -> bool:
    """Whether ``user`` may drop every live sender link of the project (deleting it unlinks them)."""
    return (
        user.org_role == 'admin'
        or not conn.execute(
            'SELECT 1 FROM project_senders ps JOIN user_oauth_connections u ON u.id = ps.sender_id'
            " WHERE ps.project_id = %s AND u.status <> 'disconnected' AND u.user_id <> %s LIMIT 1",
            (project_id, user.id),
        ).fetchone()
    )


def attach(user_id: str, project_id: str, provider: str, *, account: str, cols: dict) -> Result[None]:
    """Store an account just connected by ``user_id`` and make it one of the project's senders.

    The account is found by its verified identity ``account``: an account the organization already holds is
    re-authorised in place and keeps its connector; a new one is a new row, the caller its connector. A mailbox
    connected before #307 has no identity yet: its connector (or an admin) reconnecting the same address in a
    project it serves records one in place, or moves the project's link and conversations to the account that
    already records it; a colleague connecting that address is refused (``INTEGRATIONS.REPLACE_FORBIDDEN``); another
    address is a new account. With no identity from the provider nothing is stored (``INTEGRATIONS.PROFILE_UNAVAILABLE``). A mailbox
    joins the project's others. A LinkedIn account replaces the project's current one only for whoever connected
    that one or an admin (``INTEGRATIONS.REPLACE_FORBIDDEN``); a replaced account stays with the organization and
    its other projects. Nothing is stored on a refusal.
    """
    try:
        return _attach(user_id, project_id, provider, account=account, cols=cols)
    except psycopg.errors.UniqueViolation as e:
        if e.diag.constraint_name == 'user_oauth_connections_org_account_key':
            # A concurrent connect of the same new account committed first; this one now finds it and re-authorises it.
            return _attach(user_id, project_id, provider, account=account, cols=cols)
        if e.diag.constraint_name != 'idx_user_oauth_connections_provider_account':
            raise
        # #317: a LinkedIn account is one row across organizations
        return err('INTEGRATIONS.ALREADY_CONNECTED_ELSEWHERE', detail='this account is connected by another user')


def _attach(user_id: str, project_id: str, provider: str, *, account: str, cols: dict) -> Result[None]:
    names = list(cols)
    sets = ', '.join(f'{k} = %s' for k in names)

    def update(conn, sender_id) -> None:
        conn.execute(
            f'UPDATE user_oauth_connections SET {sets}, user_id = coalesce(user_id, %s),'
            " status = 'connected', status_detail = NULL, connected_at = now() WHERE id = %s",
            # #333: a sender whose connector deleted their account is adopted by whoever reconnects it
            (*cols.values(), user_id, sender_id),
        )

    if not account:  # the provider's profile told us no identity: an unidentifiable sender is never stored
        return err(
            'INTEGRATIONS.PROFILE_UNAVAILABLE', detail='the provider did not say which account this is, try again'
        )
    with get_conn() as conn:
        caller = conn.execute(
            'SELECT p.organization_id, m.role FROM projects p'
            ' JOIN member m ON m."organizationId" = p.organization_id AND m."userId" = %s WHERE p.id = %s',
            (user_id, project_id),
        ).fetchone()
        if caller is None:
            return err('INTEGRATIONS.INVALID_STATE', detail="not a member of this project's team")
        org, admin = caller['organization_id'], caller['role'] == 'admin'
        sender_pool.lock_campaigns(conn, project_id)
        _lock_in_id_order(conn, project_id, org, provider, account=account, label=cols.get('account_label'))
        current = _replaced(conn, project_id, provider)
        existing = (
            account
            and conn.execute(
                'SELECT * FROM user_oauth_connections WHERE organization_id = %s AND provider = %s'
                ' AND provider_account_id = %s FOR UPDATE',
                (org, provider, account),
            ).fetchone()
        )
        legacy = _legacy_row(conn, project_id, provider, cols.get('account_label'))
        if provider == 'unipile' and existing and str(existing['user_id']) != user_id and not admin:
            # A Unipile account id is named by the caller, not proven by an OAuth login as a mailbox is.
            return err(
                'INTEGRATIONS.REPLACE_FORBIDDEN',
                detail="this LinkedIn account is a colleague's: only they or an admin can re-authorise or link it",
            )

        def manages(row: dict) -> bool:
            return str(row['user_id']) == user_id or admin

        if current and existing and current['id'] == existing['id']:
            update(conn, current['id'])  # the project's own account, re-authorised
            return ok(None)
        if legacy and manages(legacy):
            if not existing:
                update(conn, legacy['id'])  # the same mailbox from before #307 records its identity
            else:
                # The account's address first: the pins follow it only to the address the provider just confirmed.
                update(conn, existing['id'])
                _supersede(conn, project_id, legacy['id'], existing['id'])
            return ok(None)
        if (
            legacy
            and legacy['status'] != 'disconnected'
            and not (existing and _is_linked(conn, project_id, existing['id']))
        ):
            return err('INTEGRATIONS.REPLACE_FORBIDDEN', detail=_LEGACY_FORBIDDEN)
        if current and current['status'] != 'disconnected' and not manages(current):
            return err(
                'INTEGRATIONS.REPLACE_FORBIDDEN',
                detail="this client already sends from a colleague's account: only they or an admin can replace it",
            )
        if existing:
            update(conn, existing['id'])
            sender_id = existing['id']
        else:
            sender_id = conn.execute(
                f'INSERT INTO user_oauth_connections (organization_id, user_id, provider, {", ".join(names)},'
                ' status, status_detail, connected_at)'
                f" VALUES (%s, %s, %s, {', '.join(['%s'] * len(names))}, 'connected', NULL, now()) RETURNING id",
                (org, user_id, provider, *cols.values()),
            ).fetchone()['id']
        if _link(conn, project_id, sender_id, provider, org):
            _linked_now(conn, project_id, sender_id)
    return ok(None)


# ── Linking an account the organization already holds to another project (#327) ──


def accounts(user: CurrentUser) -> list[dict]:
    """The organization's live senders, each with the projects using it. ``can_manage``: the caller may link or
    unlink it (its connector or an admin)."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT u.id, u.provider, u.account_label, u.status, u.user_id, c.name AS connected_by,
                      ARRAY(SELECT ps.project_id::text FROM project_senders ps JOIN projects p ON p.id = ps.project_id
                            WHERE ps.sender_id = u.id ORDER BY p.created_at) AS project_ids
               FROM user_oauth_connections u LEFT JOIN "user" c ON c.id = u.user_id
               WHERE u.organization_id = %s AND u.status <> 'disconnected'
               ORDER BY u.provider, u.account_label, u.created_at""",
            (user.org_id,),
        ).fetchall()
    return [
        {
            **{k: r[k] for k in ('id', 'provider', 'account_label', 'status', 'connected_by', 'project_ids')},
            'can_manage': may_disconnect(r, user),
        }
        for r in rows
    ]


def _managed_sender(conn, user: CurrentUser, sender_id, *, live: bool) -> Result[dict]:
    row = conn.execute(
        'SELECT * FROM user_oauth_connections WHERE id = %s AND organization_id = %s'
        + (" AND status <> 'disconnected'" if live else '')
        + ' FOR UPDATE',
        (sender_id, user.org_id),
    ).fetchone()
    if row is None:
        return err('INTEGRATIONS.SENDER_NOT_FOUND', detail='no such account in this team')
    if not may_disconnect(row, user):
        return err('INTEGRATIONS.LINK_FORBIDDEN', detail='only whoever connected it or an admin can move it')
    return ok(row)


def link(user: CurrentUser, sender_id, project_id) -> Result[None]:
    """Make a live sender one of the project's senders. Replacing the project's LinkedIn account follows ``attach``:
    only for whoever connected that one or an admin (``INTEGRATIONS.REPLACE_FORBIDDEN``)."""
    with get_conn() as conn:
        sender_pool.lock_campaigns(conn, project_id)
        row = conn.execute(
            'SELECT provider, account_label FROM user_oauth_connections WHERE id = %s AND organization_id = %s',
            (sender_id, user.org_id),
        ).fetchone()
        if row:
            _lock_in_id_order(
                conn, project_id, user.org_id, row['provider'], sender_id=sender_id, label=row['account_label']
            )
        sender = _managed_sender(conn, user, sender_id, live=True)
        if is_err(sender):
            return sender
        if not conn.execute(
            'SELECT 1 FROM projects WHERE id = %s AND organization_id = %s', (project_id, user.org_id)
        ).fetchone():
            return err('INTEGRATIONS.PROJECT_NOT_FOUND', detail='no such client in this team')
        provider = sender.value['provider']
        if (
            provider in _MAILBOXES
            and not sender.value['provider_account_id']
            and _twin_account(conn, project_id, provider, sender.value['account_label'])
        ):
            return ok(None)
        legacy = (
            _legacy_row(conn, project_id, provider, sender.value['account_label'])
            if sender.value['provider_account_id']
            else None
        )
        if legacy:
            if may_disconnect(legacy, user):
                _supersede(conn, project_id, legacy['id'], sender_id)
                return ok(None)
            if legacy['status'] != 'disconnected' and not _is_linked(conn, project_id, sender_id):
                return err('INTEGRATIONS.REPLACE_FORBIDDEN', detail=_LEGACY_FORBIDDEN)
        current = _replaced(conn, project_id, provider)
        # The same sender passes: the caller manages it.
        if current and current['status'] != 'disconnected' and not may_disconnect(current, user):
            return err(
                'INTEGRATIONS.REPLACE_FORBIDDEN',
                detail="this client already sends from a colleague's account: only they or an admin can replace it",
            )
        if _link(conn, project_id, sender_id, provider, user.org_id):
            _linked_now(conn, project_id, sender_id)
    return ok(None)


def unlink(user: CurrentUser, sender_id, project_id) -> Result[None]:
    """The project stops using the sender; the sender stays with the organization, connected, even with no project
    left (disconnecting is its own route)."""
    with get_conn() as conn:
        sender_pool.lock_campaigns(conn, project_id)
        lock_mailbox_twins(conn, user.org_id, sender_id)
        sender = _managed_sender(conn, user, sender_id, live=False)
        if is_err(sender):
            return sender
        if not conn.execute(
            'DELETE FROM project_senders WHERE project_id = %s AND sender_id = %s', (project_id, sender_id)
        ).rowcount:
            return err('INTEGRATIONS.LINK_NOT_FOUND', detail='this client does not use this account')
        # ponytail: reconciles the whole client, so it may move pins of another legacy/account pair it did not lock;
        # after the backfill no path leaves such state.
        conn.execute('SELECT reconcile_legacy_pins(%s)', (project_id,))
    return ok(None)
