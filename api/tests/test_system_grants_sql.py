"""What zenbree_system may write (PR 14 of the organization-scoping run).

A ``System`` scope runs as zenbree_system, which bypasses row-level security, so a ``System`` block that forgets its
organization reads every organization. It may not also write every organization: the role holds INSERT, UPDATE and
DELETE on a tenant relation only where a ``System`` code path writes it, by column for an UPDATE. SYSTEM_WRITES is
that list with the reasons; the catalog test fails on any grant not in it, so a new grant is a visible diff here.

A tenant relation is a table or view in public that is not labelled ``zb:unscoped``. The unscoped tables (Better Auth,
settings, the cost tables) keep the grants of api/db/20261013195148_row_level_security.sql.
"""

import pytest

from tests.test_agent import db as db
from tests.test_agent import needs_db
from tests.test_scope_sql import refused, scoped
from tests.test_scope_sql import world as world

pytestmark = needs_db

# The same predicate as api/db/20261014095833_system_role_grants.sql, which revokes on these relations.
TENANT_RELATIONS = """
SELECT c.oid, c.relname FROM pg_class c
WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p', 'v') AND c.relname <> 'schema_migrations'
  AND coalesce(obj_description(c.oid, 'pg_class'), '') !~ '^zb:unscoped (identity|global|cost):'
"""

_ACCOUNT_GONE = 'ACCOUNT_DELETE, and BACK_OFFICE before an admin deletes a user'

#: (relation, privilege): (the columns of a column grant, or None for the whole relation; the reasons that write it).
SYSTEM_WRITES: dict[tuple[str, str], tuple[tuple[str, ...] | None, str]] = {
    ('auto_topups', 'UPDATE'): (
        ('awaiting_card', 'charging_since', 'enabled', 'last_error', 'updated_at'),
        f'STRIPE_WEBHOOK: a card setup switches it on, a paid invoice ends the charge claim; {_ACCOUNT_GONE}: off',
    ),
    ('credit_transactions', 'INSERT'): (
        None,
        'RESERVATION_REAPER: the refund of a released or settled hold; STRIPE_WEBHOOK, BACK_OFFICE: increment_credits'
        ' and deduct_credits',
    ),
    ('credit_transactions', 'UPDATE'): (
        ('settlement_balance_after', 'settlement_net_credits', 'settlement_refund_amount', 'status', 'type'),
        'RESERVATION_REAPER: settle_hold and commit_settling_reservations close a stale hold (and lock it)',
    ),
    ('csv_imports', 'UPDATE'): (
        ('content', 'discarded', 'error', 'status', 'updated_at'),
        'CSV_REAPER: a stuck import fails, an old one drops its file',
    ),
    ('exclusions', 'DELETE'): (None, 'OPT_OUT: the undo of a link unsubscribe'),
    ('exclusions', 'INSERT'): (None, 'OPT_OUT: an opt-out excludes the person in every client that wrote to them'),
    ('exclusions', 'UPDATE'): (('origin', 'source'), 'OPT_OUT: an opt-out takes over an onboarding rule'),
    ('jobs', 'UPDATE'): (
        ('error', 'finished_at', 'status', 'updated_at'),
        "STALE_JOBS: a new turn fails its user's dead jobs in every organization",
    ),
    ('lookup_claims', 'DELETE'): (None, 'RESERVATION_REAPER: the claims of jobs that are no longer live'),
    ('notifications', 'INSERT'): (None, 'STRIPE_WEBHOOK: plan_renewed to the admins of the paying organization'),
    ('notifications', 'UPDATE'): (('email_pending', 'emailed_at'), 'DIGESTS: the digest takes its queue'),
    ('outreach_campaign_sends', 'UPDATE'): (
        ('error_code', 'status'),
        'OPT_OUT: the pending sends of a person who opted out are skipped',
    ),
    ('outreach_campaign_targets', 'UPDATE'): (
        ('paused_until', 'replied_at', 'sender_id', 'status'),
        f'OPT_OUT: a person who opted out stops in every campaign; {_ACCOUNT_GONE}: legacy mailbox pins follow links',
    ),
    ('outreach_campaign_senders', 'INSERT'): (None, f'{_ACCOUNT_GONE}: restore pools of moved legacy conversations'),
    ('outreach_campaigns', 'UPDATE'): (
        ('former_owner_id',),
        f'{_ACCOUNT_GONE}: the campaigns keep the creator who is about to be deleted',
    ),
    ('outreach_inbound_messages', 'INSERT'): (None, 'OPT_OUT: an opt-out by reply is filed as System'),
    ('outreach_stops', 'INSERT'): (
        None,
        'OPT_OUT: an opt-out queues the Instantly stop of the person in every project',
    ),
    ('plan_expiry_owed', 'INSERT'): (None, 'STRIPE_WEBHOOK: a renewal records the allowance an open hold keeps'),
    ('plan_expiry_owed', 'UPDATE'): (None, 'STRIPE_WEBHOOK: a second renewal adds to the debt of the same hold'),
    ('project_senders', 'DELETE'): (None, f'{_ACCOUNT_GONE}: the senders of the account leave every client'),
    ('projects', 'UPDATE'): (
        ('id',),
        f'OPT_OUT: wait for enrollments; {_ACCOUNT_GONE}: lock clients before campaigns and accounts (row locks only)',
    ),
    ('purchases', 'INSERT'): (None, 'STRIPE_WEBHOOK: a paid checkout or invoice'),
    ('purchases', 'UPDATE'): (('refunded_cents',), 'STRIPE_WEBHOOK: a refund'),
    ('push_subscriptions', 'INSERT'): (None, "PUSH_TAKEOVER: a browser's endpoint, whoever held it"),
    ('push_subscriptions', 'UPDATE'): (
        ('auth', 'created_at', 'p256dh', 'user_id'),
        'PUSH_TAKEOVER: a new user on the same browser takes the endpoint over',
    ),
    ('row_charges', 'UPDATE'): (('settled_at', 'state'), 'RESERVATION_REAPER: settle_hold charges the held rows'),
    ('subscriptions', 'INSERT'): (None, 'STRIPE_WEBHOOK: the mirror of a Stripe subscription'),
    ('subscriptions', 'UPDATE'): (
        (
            'cancel_at',
            'cancel_at_period_end',
            'current_period_end',
            'event_created',
            'plan_id',
            'scheduled_plan_id',
            'status',
            'updated_at',
        ),
        f'STRIPE_WEBHOOK: the mirror follows Stripe; {_ACCOUNT_GONE}: the cancelled subscription',
    ),
    ('user_credits', 'INSERT'): (
        None,
        'STRIPE_WEBHOOK, BACK_OFFICE: increment_credits opens a wallet that the signup hook did not',
    ),
    ('user_credits', 'UPDATE'): (
        ('credits_remaining', 'updated_at'),
        'STRIPE_WEBHOOK, BACK_OFFICE: the credit functions; RESERVATION_REAPER: a released or settled hold (and the'
        ' row lock of each)',
    ),
    ('user_oauth_connections', 'UPDATE'): (
        ('granted_scopes', 'provider_account_id', 'refresh_token_encrypted', 'status', 'status_detail', 'updated_at'),
        f'{_ACCOUNT_GONE}: the senders of the account are disconnected and their tokens dropped',
    ),
}


def _system_writes(db) -> dict[tuple[str, str], tuple[str, ...] | None]:
    """What zenbree_system holds beyond SELECT on the tenant relations, as SYSTEM_WRITES spells it."""
    rows = db.execute(f"""
        WITH tenant AS ({TENANT_RELATIONS})
        SELECT t.relname, a.privilege_type, NULL::name AS col
        FROM tenant t JOIN pg_class c ON c.oid = t.oid CROSS JOIN LATERAL aclexplode(c.relacl) a
        WHERE a.grantee = 'zenbree_system'::regrole AND a.privilege_type <> 'SELECT'
        UNION ALL
        SELECT t.relname, a.privilege_type, att.attname
        FROM tenant t JOIN pg_attribute att ON att.attrelid = t.oid CROSS JOIN LATERAL aclexplode(att.attacl) a
        WHERE a.grantee = 'zenbree_system'::regrole AND a.privilege_type <> 'SELECT'
    """).fetchall()
    found: dict[tuple[str, str], list[str] | None] = {}
    for r in rows:
        key = (r['relname'], r['privilege_type'])
        if r['col'] is None:
            found[key] = None
        elif found.get(key, []) is not None:
            found.setdefault(key, []).append(r['col'])
    return {k: v and tuple(sorted(v)) for k, v in found.items()}


def test_the_system_role_writes_only_the_listed_tenant_relations_and_columns(db):
    """Catches: a grant to zenbree_system that nobody reviewed, such as one a migration adds for a new System path."""
    wanted = {k: columns for k, (columns, _) in SYSTEM_WRITES.items()}
    found = _system_writes(db)
    extra = {k: v for k, v in found.items() if wanted.get(k, 'missing') != v}
    missing = {k: v for k, v in wanted.items() if found.get(k, 'missing') != v}
    assert not extra and not missing, (
        f'zenbree_system holds writes that SYSTEM_WRITES does not list: {extra}\n'
        f'SYSTEM_WRITES lists writes that zenbree_system does not hold: {missing}\n'
        'A System path that writes a tenant relation needs the grant in its migration and the entry here, with the'
        ' SystemReason that writes it. A System path that should not write: run it as acting_for(org, why) instead.'
    )


def test_the_system_role_cannot_write_organization_data_outside_its_grants(db, world):
    """Catches: a System block that forgets its organization and writes into every organization."""
    with scoped(db, role='zenbree_system'):
        refused(
            db,
            'INSERT INTO lists (organization_id, project_id, user_id, name, entity) SELECT organization_id,'
            " project_id, user_id, 'x', 'company' FROM lists WHERE id = %s",
            (world['list_a'],),
        )
        refused(db, "UPDATE list_rows SET cells = '{}'::jsonb")
        refused(db, 'DELETE FROM projects WHERE organization_id = %s', (world['b'],))
        refused(db, "UPDATE saved_searches SET name = 'x'")
        # A granted relation, a column that is not granted.
        refused(db, 'UPDATE user_credits SET organization_id = %s', (world['a'],))
        refused(db, 'UPDATE jobs SET user_id = %s', (world['u'],))
        # keep_campaign_reply_key runs as the caller. No System path deletes a campaign, so the role cannot: a
        # campaign delete that drops its reply key without a trace is not possible as System.
        refused(db, 'DELETE FROM outreach_campaigns')
        refused(db, 'DELETE FROM outreach_campaign_reply_keys')
        refused(db, 'TRUNCATE lists')
        # The row locks of the reaper and the Stripe webhook need UPDATE on one column, which they hold.
        db.execute('SELECT 1 FROM user_credits WHERE organization_id = %s FOR UPDATE', (world['a'],))
        db.execute('SELECT 1 FROM credit_transactions FOR UPDATE SKIP LOCKED')
    assert db.execute('SELECT count(*) AS n FROM projects').fetchone()['n'] == 2


def test_a_new_table_gives_the_system_role_select_only(db):
    """Catches: the event trigger granting writes on a new table, which then waits for nobody to notice."""
    db.execute('CREATE TABLE zt_new (id int, organization_id uuid)')
    db.execute('CREATE VIEW zt_new_view AS SELECT id FROM zt_new')
    for relation in ('zt_new', 'zt_new_view'):
        held = db.execute(
            "SELECT p, has_table_privilege('zenbree_system', %s, p) AS s, has_table_privilege('zenbree_app', %s, p) AS a"
            " FROM unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']) p",
            (relation, relation),
        ).fetchall()
        assert {r['p']: (r['s'], r['a']) for r in held} == {
            'SELECT': (True, True),
            'INSERT': (False, True),
            'UPDATE': (False, True),
            'DELETE': (False, True),
        }, relation
    with scoped(db, role='zenbree_system'):
        refused(db, 'INSERT INTO zt_new VALUES (1, NULL)')


@pytest.mark.parametrize('table', ['settings', 'webhook_events', 'llm_usage', 'organization', 'member'])
def test_the_unscoped_tables_keep_their_grants(db, table):
    """The unscoped tables hold no organization's rows: their grants stay those of the row-level security run."""
    held = db.execute(
        "SELECT p FROM unnest(ARRAY['INSERT', 'UPDATE', 'DELETE']) p WHERE has_table_privilege('zenbree_system', %s, p)"
        " OR (p = 'UPDATE' AND has_any_column_privilege('zenbree_system', %s, 'UPDATE'))",
        (table, table),
    ).fetchall()
    assert {r['p'] for r in held} == {
        'settings': {'INSERT', 'UPDATE', 'DELETE'},
        'webhook_events': {'INSERT', 'UPDATE'},
        'llm_usage': {'INSERT', 'DELETE'},
        'organization': {'INSERT', 'UPDATE'},
        'member': {'INSERT'},
    }[table]
