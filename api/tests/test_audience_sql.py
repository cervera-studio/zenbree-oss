"""Audience identity (#937): linking, conflicts, backfill, scope and what follows an alias."""

import threading
import time

import psycopg
import pytest
from celery.exceptions import SoftTimeLimitExceeded
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.audience import aliases, beat, conflicts
from app.audience import link as link_module
from app.audience.follow import alias_map, excluded_aliases, find_identity, same_person
from app.audience.link import MAX_ATTEMPTS, attach_alias, attach_new_alias, link_pending, mark_rows
from app.core.config import settings
from app.core.migrate import MIGRATIONS
from app.core.system_reasons import SystemReason
from app.exclusions.rules import is_excluded, load_rules, normalise
from app.lists.rows import add_rows, apply_row_updates
from app.scheduling import runner
from tests.conftest import call_for, call_system, conninfo_of, member, project, scoped_conn, wait_for_a_lock
from tests.test_agent import _user, needs_db
from tests.test_agent import client as client
from tests.test_agent import db as db
from tests.test_home_overview import _list

MIGRATION = '20261016190050_audience_identity.sql'
LI = 'https://www.linkedin.com/in/ada-rossi/'
LI_STORED = 'linkedin.com/in/ada-rossi'


def _identity(db, row_id):
    row = db.execute(
        'SELECT identity_id::text AS id FROM audience_row_links WHERE list_row_id = %s', (row_id,)
    ).fetchone()
    return row['id'] if row else None


def _settle(db):
    """What the link task does to the marked rows, on the test's connection."""
    for m in db.execute(
        'SELECT list_row_id, source FROM audience_link_queue ORDER BY queued_at, list_row_id'
    ).fetchall():
        db.execute('SELECT audience_link_row(%s, %s)', (m['list_row_id'], m['source']))
    db.execute('DELETE FROM audience_link_queue')


def _add(db, list_id, entity, rows):
    ids = add_rows(db, list_id, entity, rows)
    _settle(db)
    return ids


def _update(db, list_id, entity, updates):
    landed = apply_row_updates(db, list_id, entity, updates)
    _settle(db)
    return landed


def _link(db, row_ids):
    mark_rows(db, row_ids)
    _settle(db)


def _row(db, list_id, cells, entity='person'):
    """One row through ``add_rows``, as a tool or an import writes it, linked as the task links it."""
    (rid,) = _add(db, list_id, entity, [cells])
    return rid


def _conflicts(db, status='open'):
    return db.execute('SELECT * FROM audience_conflicts WHERE status = %s', (status,)).fetchall()


SAMPLES = [
    ('email', ' Ada@Acme.COM '),
    ('email', 'not an email'),
    ('email', 'a@b'),
    ('linkedin_url', 'https://www.linkedin.com/in/Ada-Rossi/?utm=1'),
    ('linkedin_url', 'www.linkedin.com/in/ada'),
    ('linkedin_url', 'linkedin.com/in/ada/'),
    ('email', 'info@acme.com'),
    ('email', 'Sales@acme.com'),
    ('email', 'ada.info@acme.com'),
    ('linkedin_url', 'www.linkedin.com/company/acme//'),
    ('linkedin_url', 'https://example.com/in/ada'),
    ('domain', 'https://www.Acme.com/about?x=1'),
    ('domain', 'eu.acme.co.uk'),
    ('domain', 'linkedin.com'),
    ('domain', 'company.linkedin.com'),
    ('domain', 'facebook.com/acme'),
    ('domain', 'localhost'),
    ('domain', ''),
]


@needs_db
@pytest.mark.parametrize(('kind', 'raw'), SAMPLES)
def test_sql_and_python_normalize_an_alias_alike(db, kind, raw):
    sql = db.execute('SELECT audience_alias_value(%s, %s) AS v', (kind, raw)).fetchone()['v']
    assert sql == aliases.normalize(kind, raw)


@pytest.mark.parametrize(('kind', 'raw'), SAMPLES)
def test_an_alias_is_stored_as_an_exclusion_rule_stores_it(kind, raw):
    stored = aliases.normalize(kind, raw)
    if stored is not None:
        assert stored == normalise(kind, raw)  # one normalizer: a followed alias matches the cell it came from


@needs_db
def test_the_same_exact_alias_in_two_lists_is_one_identity_and_two_rows(db):
    uid = _user(db)
    one, two = _list(db, uid, 'One'), _list(db, uid, 'Two')
    a = _row(db, one, {'full_name': 'Ada', 'email': 'Ada@Acme.com', 'source': 'apollo'})
    b = _row(db, two, {'full_name': 'Ada R.', 'email': 'ada@acme.com'})
    assert a != b and _identity(db, a) == _identity(db, b) is not None
    assert db.execute('SELECT count(*) AS n FROM list_rows').fetchone()['n'] == 2  # billing counts rows, as before
    alias = db.execute('SELECT kind, value, source FROM audience_aliases').fetchone()
    assert (alias['kind'], alias['value'], alias['source']) == ('email', 'ada@acme.com', 'apollo')


@needs_db
def test_a_name_alone_never_links_or_creates_an_identity(db):
    uid = _user(db)
    one, two = _list(db, uid, 'One'), _list(db, uid, 'Two')
    a = _row(db, one, {'full_name': 'Ada Rossi', 'company_name': 'Acme'})
    b = _row(db, two, {'full_name': 'Ada Rossi', 'company_name': 'Acme', 'title': 'CEO'})
    assert _identity(db, a) is None and _identity(db, b) is None
    assert db.execute('SELECT count(*) AS n FROM audience_identities').fetchone()['n'] == 0
    c1 = _row(db, _list(db, uid, 'Cos', 'company'), {'name': 'Acme'}, 'company')
    assert _identity(db, c1) is None


@needs_db
def test_a_later_alias_joins_the_identity_and_the_provenance_stays(db):
    uid = _user(db)
    lid = _list(db, uid, 'One')
    rid = _row(db, lid, {'full_name': 'Ada', 'email': 'ada@acme.com', 'source': 'apollo'})
    first = db.execute(  # a day back: a later write of now() can never equal it
        "UPDATE audience_aliases SET observed_at = observed_at - interval '1 day' RETURNING observed_at"
    ).fetchone()['observed_at']
    _update(db, lid, 'person', [{'row_id': rid, 'cells': {'linkedin_url': LI, 'source': 'aiark'}}])
    got = {r['kind']: r for r in db.execute('SELECT kind, value, source, observed_at FROM audience_aliases')}
    assert got['linkedin_url']['value'] == LI_STORED
    assert got['email']['observed_at'] == first and got['email']['source'] == 'apollo'
    assert len({_identity(db, rid)}) == 1


@needs_db
def test_a_provider_refresh_adds_a_value_and_keeps_the_trusted_one(db):
    uid = _user(db)
    lid = _list(db, uid, 'One')
    rid = _row(db, lid, {'full_name': 'Ada', 'email': 'ada@acme.com', 'source': 'hubspot'})
    _update(db, lid, 'person', [{'row_id': rid, 'cells': {'email': 'ada@new.test', 'source': 'apollo'}}])
    got = {r['value']: r['source'] for r in db.execute('SELECT value, source FROM audience_aliases')}
    assert got == {'ada@acme.com': 'hubspot', 'ada@new.test': 'apollo'}
    assert db.execute('SELECT count(DISTINCT identity_id) AS n FROM audience_aliases').fetchone()['n'] == 1


@needs_db
def test_a_company_follows_its_domain_and_never_a_shared_host(db):
    uid = _user(db)
    one, two = _list(db, uid, 'One', 'company'), _list(db, uid, 'Two', 'company')
    a = _row(db, one, {'name': 'Acme', 'domain': 'acme.com'}, 'company')
    b = _row(db, two, {'name': 'Acme Spa', 'website': 'https://www.acme.com/it'}, 'company')
    c = _row(db, two, {'name': 'Other', 'website': 'https://www.facebook.com/other'}, 'company')
    assert _identity(db, a) == _identity(db, b) is not None
    assert _identity(db, c) is None


@needs_db
def test_two_identities_tied_by_one_row_become_a_conflict_and_never_merge(db):
    uid = _user(db)
    lid = _list(db, uid, 'One')
    a = _row(db, lid, {'full_name': 'Ada', 'email': 'ada@acme.com', 'source': 'apollo'})
    b = _row(db, lid, {'full_name': 'Ada R', 'linkedin_url': LI, 'source': 'aiark'})
    assert _identity(db, a) != _identity(db, b)
    _update(db, lid, 'person', [{'row_id': b, 'cells': {'email': 'ada@acme.com'}}])
    (conflict,) = _conflicts(db)
    assert {str(conflict['identity_a']), str(conflict['identity_b'])} == {_identity(db, a), _identity(db, b)}
    assert str(conflict['list_row_id']) == b
    assert sorted(e['source'] for e in conflict['evidence']) == ['aiark', 'apollo']  # both sides, with provenance
    assert _identity(db, a) != _identity(db, b)  # nothing merged, the email stayed with the first
    assert db.execute("SELECT identity_id::text AS i FROM audience_aliases WHERE value = 'ada@acme.com'").fetchone()[
        'i'
    ] == _identity(db, a)
    _link(db, [a, b])  # raised again: still one conflict
    assert len(_conflicts(db)) == 1


@needs_db
def test_the_same_alias_in_two_projects_is_two_identities(db):
    uid = _user(db)
    project(db, uid)  # the default project exists before the other one
    other = db.execute(
        'INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, %s) RETURNING id',
        (member(db, uid).org_id, uid, 'Other client'),
    ).fetchone()['id']
    a = _row(db, _list(db, uid, 'Mine'), {'email': 'ada@acme.com'})
    b = _row(db, _list(db, uid, 'Theirs', pid=other), {'email': 'ada@acme.com'})
    assert _identity(db, a) != _identity(db, b)
    assert not _conflicts(db)


@needs_db
def test_a_deleted_row_and_a_deleted_list_keep_the_identity(db):
    uid = _user(db)
    lid = _list(db, uid, 'One')
    rid = _row(db, lid, {'email': 'ada@acme.com'})
    ident = _identity(db, rid)
    db.execute('DELETE FROM list_rows WHERE id = %s', (rid,))
    assert db.execute('SELECT count(*) AS n FROM audience_row_links').fetchone()['n'] == 0
    other = _list(db, uid, 'Two')
    again = _row(db, other, {'email': 'ADA@acme.com'})
    assert _identity(db, again) == ident  # the next row of that person finds it
    db.execute('DELETE FROM lists WHERE id = %s', (other,))
    assert db.execute('SELECT count(*) AS n FROM audience_identities WHERE id = %s', (ident,)).fetchone()['n'] == 1


@needs_db
def test_two_connections_that_meet_one_alias_make_one_identity(db):
    uid = _user(db)
    one, two = _list(db, uid, 'One'), _list(db, uid, 'Two')
    a = _row(db, one, {'full_name': 'Ada'})
    b = _row(db, two, {'full_name': 'Ada'})
    first = psycopg.connect(settings.database_url, row_factory=dict_row)  # a turn that has not committed yet
    second = psycopg.connect(settings.database_url, row_factory=dict_row)
    try:
        for c, rid in ((first, a), (second, b)):
            c.execute('UPDATE list_rows SET cells = %s WHERE id = %s', (Jsonb({'email': 'ada@acme.com'}), rid))
            c.commit()
        first.execute('SELECT audience_link_row(%s)', (a,))  # the first link is open: it holds the value
        done = []

        def link_second():
            _link_on(second, b)
            done.append(True)

        t = threading.Thread(target=link_second)
        t.start()
        t.join(0.5)
        assert t.is_alive() and not done  # it waits for the first turn instead of creating a second identity
        first.commit()
        t.join(10)
        assert done
    finally:
        first.close()
        second.close()
    assert _identity(db, a) == _identity(db, b) is not None
    assert db.execute('SELECT count(*) AS n FROM audience_identities').fetchone()['n'] == 1
    assert not _conflicts(db)


@needs_db
def test_a_provider_id_attaches_once_and_conflicts_on_another_identity(db):
    uid = _user(db)
    pid = project(db, uid)
    lid = _list(db, uid, 'One')
    a = _identity(db, _row(db, lid, {'email': 'ada@acme.com'}))
    b = _identity(db, _row(db, lid, {'email': 'bob@acme.com'}))
    ns = 'hubspot:123:contact'
    assert attach_alias(db, a, 'crm_object', ns, '501', 'hubspot') == 'attached'
    assert attach_alias(db, a, 'crm_object', ns, '501', 'hubspot') == 'known'
    assert attach_alias(db, b, 'crm_object', ns, '501', 'hubspot') == 'conflict'
    assert attach_alias(db, b, 'external_lead', 'instantly:ws-1', '501', 'instantly') == 'attached'  # own namespace
    assert find_identity(db, pid, 'crm_object', '501', ns) == a
    assert find_identity(db, pid, 'external_lead', '501', 'instantly:ws-1') == b
    assert find_identity(db, pid, 'email', ' ADA@acme.com ') == a
    assert find_identity(db, pid, 'crm_object', '501', 'hubspot:999:contact') is None
    assert len(_conflicts(db)) == 1
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        attach_alias(db, a, 'crm_object', '', '777', 'hubspot')


@needs_db
def test_an_opt_out_of_one_alias_covers_the_others_in_this_project_only(db):
    uid = _user(db)
    pid = project(db, uid)
    other = db.execute(
        'INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, %s) RETURNING id',
        (member(db, uid).org_id, uid, 'Other client'),
    ).fetchone()['id']
    lid = _list(db, uid, 'One')
    _row(db, lid, {'email': 'ada@acme.com', 'linkedin_url': LI})
    _row(db, _list(db, uid, 'Theirs', pid=other), {'email': 'ada@acme.com', 'linkedin_url': LI})
    db.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value, origin) VALUES (%s, %s, 'email', 'ada@acme.com', 'unsubscribed')",
        (pid, uid),
    )
    assert is_excluded(load_rules(db, pid), {'linkedin_url': 'https://linkedin.com/in/ADA-rossi'})
    assert not is_excluded(load_rules(db, other), {'linkedin_url': LI})  # the other project never opted out
    db.execute('DELETE FROM exclusions WHERE project_id = %s', (pid,))
    assert not is_excluded(load_rules(db, pid), {'linkedin_url': LI})  # an undone opt-out lifts the alias too


@needs_db
def test_enrollment_sees_one_person_behind_two_aliases(db):
    uid = _user(db)
    pid = project(db, uid)
    lid = _list(db, uid, 'One')
    _row(db, lid, {'email': 'ada@acme.com', 'linkedin_url': LI})
    target = {
        'email': 'ada@acme.com',
        'linkedin_url': '',
        'campaign_id': 'other',
        'status': 'active',
        'replied_at': None,
        'next_step': 1,
        'campaign_status': 'active',
    }
    campaign = {'id': 'mine', 'block_prior_contacts': True, 'block_active_duplicates': True}
    row = {'linkedin_url': LI}
    held = [('email', 'ada@acme.com')]
    ids = alias_map(db, pid, held)
    assert runner.enrollment_verdict(row, campaign, ([target], [], frozenset(), ids))[0] == 'hold'
    assert (
        runner.enrollment_verdict(row, campaign, ([target], [], frozenset(), {}))[0] == 'enroll'
    )  # no identity: as before
    replied = {**target, 'status': 'replied'}
    assert runner.enrollment_verdict(row, campaign, ([replied], [], frozenset(), ids))[0] == 'skip'
    sent = [{'channel': 'email', 'recipient': 'ada@acme.com'}]
    assert runner.enrollment_verdict(row, campaign, ([], sent, frozenset(), ids))[0] == 'skip'
    assert same_person(ids, [('linkedin_url', LI)], [('email', 'ADA@acme.com')])
    assert not same_person(ids, [('linkedin_url', LI)], [('email', 'bob@acme.com')])
    assert excluded_aliases(db, pid, {('email', 'ada@acme.com')}) == {
        ('email', 'ada@acme.com'),
        ('linkedin_url', LI_STORED),
    }


@needs_db
def test_the_migration_backfills_without_touching_cells_or_merging_people(db):
    uid = _user(db)
    one, two = _list(db, uid, 'One'), _list(db, uid, 'Two')
    cos = _list(db, uid, 'Cos', 'company')

    def raw(lid, cells, n):
        return str(
            db.execute(
                'INSERT INTO list_rows (list_id, cells, dedupe_key, created_at) VALUES (%s, %s, %s,'
                ' now() - make_interval(mins => %s)) RETURNING id',
                (lid, Jsonb(cells), f'k{n}', 100 - n),
            ).fetchone()['id']
        )

    r1 = raw(one, {'email': 'a@x.test', 'source': 'apollo'}, 1)
    r2 = raw(two, {'email': 'A@X.test'}, 2)  # the same email: the same person
    r3 = raw(one, {'email': 'b@x.test', 'linkedin_url': LI}, 3)
    r4 = raw(two, {'linkedin_url': LI_STORED + '/'}, 4)  # the same profile as r3
    r5 = raw(two, {'email': 'a@x.test', 'linkedin_url': LI}, 5)  # ties r1 and r3: a conflict
    r6 = raw(one, {'full_name': 'Ada Rossi', 'company_name': 'Acme'}, 6)  # only a name
    c1 = raw(cos, {'name': 'Acme', 'domain': 'acme.com'}, 7)
    c2 = raw(cos, {'name': 'Acme Group', 'website': 'https://www.acme.com'}, 8)
    c3 = raw(cos, {'name': 'Acme'}, 9)  # the same name, no alias: no merge
    cells_before = db.execute('SELECT id, cells FROM list_rows ORDER BY id').fetchall()
    assert db.execute('SELECT count(*) AS n FROM audience_row_links').fetchone()['n'] == 0

    sql = (MIGRATIONS / MIGRATION).read_text()
    db.execute(sql[sql.index('-- Backfill, set-based') :])

    assert db.execute('SELECT id, cells FROM list_rows ORDER BY id').fetchall() == cells_before
    rows = {'r1': r1, 'r2': r2, 'r3': r3, 'r4': r4, 'r5': r5, 'r6': r6, 'c1': c1, 'c2': c2, 'c3': c3}
    ids = {k: _identity(db, v) for k, v in rows.items()}
    assert ids['r1'] == ids['r2'] is not None
    assert ids['r3'] == ids['r4'] is not None and ids['r3'] != ids['r1']
    assert ids['r5'] is None and ids['r6'] is None and ids['c3'] is None
    assert ids['c1'] == ids['c2'] is not None
    (conflict,) = _conflicts(db)
    assert {str(conflict['identity_a']), str(conflict['identity_b'])} == {ids['r1'], ids['r3']}
    assert conflict['list_row_id'] == db.execute('SELECT %s::uuid AS i', (r5,)).fetchone()['i']


@needs_db
def test_a_row_shows_its_aliases_and_another_projects_row_is_not_found(client, db):
    tc, uid = client
    mine = _row(db, _list(db, uid, 'Mine'), {'email': 'ada@acme.com', 'source': 'apollo'})
    res = tc.get(f'/v1/audience/rows/{mine}')
    assert res.status_code == 200
    body = res.json()
    assert str(body['identity_id']) == _identity(db, mine) and body['open_conflicts'] == []
    assert [(a['kind'], a['value'], a['source']) for a in body['aliases']] == [('email', 'ada@acme.com', 'apollo')]

    project(db, uid)
    other = db.execute(
        'INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, %s) RETURNING id',
        (member(db, uid).org_id, uid, 'Other client'),
    ).fetchone()['id']
    elsewhere = _row(db, _list(db, uid, 'Elsewhere', pid=other), {'email': 'x@acme.com'})
    foreign = _row(db, _list(db, _user(db), 'Foreign'), {'email': 'y@acme.com'})
    for rid in (elsewhere, foreign):
        res = tc.get(f'/v1/audience/rows/{rid}')
        assert res.status_code == 404 and res.json()['detail']['code'] == 'AUDIENCE.ROW_NOT_FOUND'
    assert (
        tc.get(f'/v1/audience/rows/{mine}', headers={'X-Project-Id': str(other)}).json()['detail']['code']
        == 'AUDIENCE.ROW_NOT_FOUND'
    )


def _conflict(db, uid):
    lid = _list(db, uid, 'One')
    a = _row(db, lid, {'email': 'ada@acme.com', 'source': 'apollo'})
    b = _row(db, lid, {'linkedin_url': LI, 'source': 'aiark'})
    _update(db, lid, 'person', [{'row_id': b, 'cells': {'email': 'ada@acme.com'}}])
    return a, b, _conflicts(db)[0]


@needs_db
def test_a_conflict_is_listed_and_a_merge_moves_aliases_and_rows(client, db):
    tc, uid = client
    a, b, conflict = _conflict(db, uid)
    listed = tc.get('/v1/audience/conflicts').json()['conflicts']
    assert [c['id'] for c in listed] == [str(conflict['id'])]
    assert sorted(len(s['aliases']) for s in listed[0]['identities']) == [1, 1]
    assert tc.get(f'/v1/audience/rows/{b}').json()['open_conflicts'] == [str(conflict['id'])]

    res = tc.post(f'/v1/audience/conflicts/{conflict["id"]}/resolve', json={'action': 'merge'})
    assert res.status_code == 200 and res.json()['status'] == 'merged'
    assert _identity(db, a) == _identity(db, b) == res.json()['identity_id']
    assert db.execute('SELECT count(DISTINCT identity_id) AS n FROM audience_aliases').fetchone()['n'] == 1
    assert tc.get('/v1/audience/conflicts').json()['conflicts'] == []
    assert tc.get('/v1/audience/conflicts?status=merged').json()['conflicts'][0]['id'] == str(conflict['id'])
    again = tc.post(f'/v1/audience/conflicts/{conflict["id"]}/resolve', json={'action': 'merge'})
    assert again.status_code == 409 and again.json()['detail']['code'] == 'AUDIENCE.CONFLICT_CLOSED'


@needs_db
def test_keeping_two_identities_apart_is_recorded_and_not_raised_again(client, db):
    tc, uid = client
    a, b, conflict = _conflict(db, uid)
    bad = tc.post(f'/v1/audience/conflicts/{conflict["id"]}/resolve', json={'action': 'merge', 'into': str(uid)})
    assert bad.status_code == 422 and bad.json()['detail']['code'] == 'AUDIENCE.CONFLICT_SIDE'
    res = tc.post(f'/v1/audience/conflicts/{conflict["id"]}/resolve', json={'action': 'keep_apart'})
    assert res.json() == {'status': 'kept_apart', 'identity_id': None}
    _link(db, [a, b])
    assert _conflicts(db) == [] and len(_conflicts(db, 'kept_apart')) == 1
    assert _identity(db, a) != _identity(db, b)


@needs_db
def test_another_projects_conflict_is_not_found_and_stays_open(client, db):
    tc, uid = client
    project(db, uid)
    stranger = _user(db)
    _, _, theirs = _conflict(db, stranger)
    res = tc.post(f'/v1/audience/conflicts/{theirs["id"]}/resolve', json={'action': 'keep_apart'})
    assert res.status_code == 404 and res.json()['detail']['code'] == 'AUDIENCE.CONFLICT_NOT_FOUND'
    assert tc.get('/v1/audience/conflicts').json()['conflicts'] == []
    assert (
        db.execute('SELECT status FROM audience_conflicts WHERE id = %s', (theirs['id'],)).fetchone()['status']
        == 'open'
    )


@needs_db
def test_linking_runs_under_row_level_security_and_one_tenant_never_reads_anothers_aliases(db, monkeypatch):
    monkeypatch.setattr('app.audience.link.get_conn', scoped_conn(db))
    mine, theirs = _user(db), _user(db)
    mine_list, their_list = _list(db, mine, 'Mine'), _list(db, theirs, 'Theirs')
    org_a, org_b = member(db, mine).org_id, member(db, theirs).org_id

    def write(list_id, email):
        with scoped_conn(db)() as conn:
            (rid,) = add_rows(conn, list_id, 'person', [{'email': email}])
            return rid

    a = call_for(org_a, write, mine_list, 'ada@acme.com')
    b = call_for(org_b, write, their_list, 'ada@acme.com')  # the same address in another organization
    assert call_for(org_a, link_pending) == 1  # each organization's task links its own marked rows only
    assert call_for(org_b, link_pending) == 1
    assert _identity(db, a) != _identity(db, b)

    def visible():
        with scoped_conn(db)() as conn:
            return conn.execute('SELECT count(*) AS n FROM audience_aliases').fetchone()['n']

    assert call_for(org_a, visible) == 1 and call_for(org_b, visible) == 1
    assert not _conflicts(db)


@needs_db
def test_the_role_addresses_of_python_and_sql_agree(db):
    for local in sorted(aliases.ROLE_LOCAL_PARTS):
        assert (
            db.execute("SELECT audience_alias_value('email', %s) AS v", (f'{local}@acme.com',)).fetchone()['v'] is None
        )
    for kind, raw in [('email', 'ada@acme.com'), ('email', 'ada.info@acme.com'), ('email', 'informer@acme.com')]:
        assert db.execute('SELECT audience_alias_value(%s, %s) AS v', (kind, raw)).fetchone()['v'] == raw


@needs_db
def test_a_role_address_never_links_people(db):
    uid = _user(db)
    one, two = _list(db, uid, 'One'), _list(db, uid, 'Two')
    a = _row(db, one, {'full_name': 'Ada', 'email': 'info@acme.com'})
    b = _row(db, two, {'full_name': 'Bob', 'email': 'Sales@Acme.com', 'linkedin_url': 'linkedin.com/in/bob'})
    assert _identity(db, a) is None  # nothing but a role address: no identity
    assert [r['value'] for r in db.execute('SELECT value FROM audience_aliases')] == ['linkedin.com/in/bob']
    assert _identity(db, b) is not None


@needs_db
def test_a_cell_without_a_scheme_matches_the_followed_alias(db):
    uid = _user(db)
    pid = project(db, uid)
    one = _list(db, uid, 'One')
    _row(db, one, {'email': 'ada@acme.com', 'linkedin_url': 'linkedin.com/in/ada/'})
    _row(db, _list(db, uid, 'Two'), {'linkedin_url': 'https://www.linkedin.com/in/ada?x=1'})
    assert db.execute('SELECT count(DISTINCT identity_id) AS n FROM audience_aliases').fetchone()['n'] == 1
    db.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value, origin) VALUES (%s, %s, 'email', 'ada@acme.com', 'manual')",
        (pid, uid),
    )
    rules = load_rules(db, pid)
    assert is_excluded(rules, {'linkedin_url': 'linkedin.com/in/ada'})  # a cell with no scheme, as stored
    assert is_excluded(rules, {'linkedin_url': 'https://www.linkedin.com/in/ada/'})


@needs_db
def test_a_hubspot_opt_out_follows_the_other_aliases_of_the_person(db, monkeypatch):
    uid = _user(db)
    pid = project(db, uid)
    _row(db, _list(db, uid, 'One'), {'email': 'ada@acme.com', 'linkedin_url': LI})
    monkeypatch.setattr(runner.gate, 'opted_out_rules', lambda conn, owner: frozenset({('email', 'ada@acme.com')}))
    campaign = {
        'project_id': pid,
        'owner_id': uid,
        'block_prior_contacts': False,
        'block_active_duplicates': False,
        'id': None,
    }
    context = runner.enrollment_context(db, campaign)
    assert runner.enrollment_verdict({'linkedin_url': LI}, campaign, context)[0] == 'skip'
    assert runner.enrollment_verdict({'linkedin_url': 'linkedin.com/in/someone-else'}, campaign, context)[0] == 'enroll'


@needs_db
def test_a_hand_edit_of_the_email_or_profile_joins_the_identity(client, db):
    tc, uid = client
    lid = _list(db, uid, 'One')
    a = _row(db, lid, {'full_name': 'Ada', 'email': 'ada@acme.com'})
    b = _row(db, lid, {'full_name': 'Ada R', 'linkedin_url': LI})
    res = tc.patch(f'/v1/lists/{lid}/rows/{a}', json={'cells': {'linkedin_url': 'https://linkedin.com/in/ada-rossi'}})
    assert res.status_code == 200
    assert (
        db.execute('SELECT source FROM audience_link_queue WHERE list_row_id = %s', (a,)).fetchone()['source']
        == 'manual'
    )
    _settle(db)  # the task that links the marked row
    # the profile a was given is already b's identity: the edit raised a conflict and merged nothing
    (conflict,) = _conflicts(db)
    assert str(conflict['list_row_id']) == a and _identity(db, a) != _identity(db, b)
    fresh = tc.patch(f'/v1/lists/{lid}/rows/{b}', json={'cells': {'email': 'bob@acme.com'}})
    assert fresh.status_code == 200
    _settle(db)
    assert (
        db.execute("SELECT source FROM audience_aliases WHERE value = 'bob@acme.com'").fetchone()['source'] == 'manual'
    )


@needs_db
def test_conflicts_come_in_pages(client, db):
    tc, uid = client
    lid = _list(db, uid, 'One')
    for n in range(3):
        a = _row(db, lid, {'email': f'p{n}@acme.com'})
        b = _row(db, lid, {'linkedin_url': f'linkedin.com/in/p{n}'})
        _update(db, lid, 'person', [{'row_id': b, 'cells': {'email': f'p{n}@acme.com'}}])
        assert a != b
    first = tc.get('/v1/audience/conflicts?limit=2').json()
    assert len(first['conflicts']) == 2 and first['next_cursor'] is not None
    rest = tc.get(f'/v1/audience/conflicts?limit=2&cursor={first["next_cursor"]}').json()
    assert len(rest['conflicts']) == 1 and rest['next_cursor'] is None
    assert len({c['id'] for c in first['conflicts'] + rest['conflicts']}) == 3
    assert tc.get('/v1/audience/conflicts?limit=0').status_code == 422


@needs_db
def test_a_lead_with_no_row_gets_an_identity_and_the_kind_must_fit_the_entity(db):
    uid = _user(db)
    pid = project(db, uid)
    ident, result = attach_new_alias(db, pid, 'person', 'external_lead', 'instantly:ws-1', 'lead-7', 'instantly')
    assert result == 'attached'
    assert attach_new_alias(db, pid, 'person', 'external_lead', 'instantly:ws-1', 'lead-7', 'instantly') == (
        ident,
        'known',
    )
    assert attach_alias(db, ident, 'email', '', 'Ada@Acme.com', 'instantly') == 'attached'
    row = _row(db, _list(db, uid, 'One'), {'email': 'ada@acme.com'})
    assert _identity(db, row) == ident  # the next list row of that person finds the lead's identity
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        attach_alias(db, ident, 'domain', '', 'acme.com', 'instantly')  # a person has no domain
    company, _ = attach_new_alias(db, pid, 'company', 'domain', '', 'https://www.acme.com/x', 'hubspot')
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        attach_alias(db, company, 'email', '', 'a@acme.com', 'hubspot')
    assert find_identity(db, pid, 'domain', 'acme.com') == company


@needs_db
def test_a_writer_that_holds_a_row_lock_never_waits_on_a_project_lock(db):
    """A turn writes new rows, a refill merges into a row the turn updates next: no deadlock (#937 review)."""
    uid = _user(db)
    lid = _list(db, uid, 'One')
    seed = _row(db, lid, {'full_name': 'Ada', 'email': 'ada@acme.com'})
    turn = psycopg.connect(settings.database_url, row_factory=dict_row)
    refill = psycopg.connect(settings.database_url, row_factory=dict_row)
    errors: list[str] = []
    try:
        add_rows(turn, lid, 'person', [{'full_name': 'New', 'email': 'new@acme.com'}])  # the turn's first write
        started = threading.Event()

        def merge():
            started.set()
            try:
                add_rows(refill, lid, 'person', [{'email': 'ada@acme.com', 'title': 'CEO'}])
                refill.commit()
            except psycopg.errors.DeadlockDetected:
                errors.append('DeadlockDetected')
                refill.rollback()

        t = threading.Thread(target=merge)
        t.start()
        started.wait(5)
        wait_for_a_lock(conninfo_of(db), 'the refill', refill.info.backend_pid, unless=t)
        try:
            apply_row_updates(
                turn, lid, 'person', [{'row_id': seed, 'cells': {'phone': '+39 1'}}]
            )  # the turn's delivery
            turn.commit()
        except psycopg.errors.DeadlockDetected:
            errors.append('DeadlockDetected')
            turn.rollback()
        t.join(10)
    finally:
        turn.close()
        refill.close()
    assert errors == []


def _grouping(db):
    links = db.execute('SELECT list_row_id::text AS r, identity_id::text AS i FROM audience_row_links').fetchall()
    groups: dict[str, set] = {}
    for link in links:
        groups.setdefault(link['i'], set()).add(link['r'])
    tied_rows = sorted(str(c['list_row_id']) for c in _conflicts(db))
    return {frozenset(g) for g in groups.values()}, tied_rows


@needs_db
def test_the_set_based_backfill_gives_what_linking_the_rows_in_turn_gives(db):
    import random

    rng = random.Random(937)  # noqa: S311 - a fixed seed for repeatable test data
    uid = _user(db)
    lists = [_list(db, uid, f'L{n}') for n in range(3)] + [_list(db, uid, 'Cos', 'company')]
    ids = []
    for n in range(80):
        lid = lists[rng.randrange(4)]
        company = lid == lists[3]
        cells = {'name': f'n{n}'}
        if company:
            if rng.random() < 0.7:
                cells['domain'] = f'co{rng.randrange(12)}.test'
            if rng.random() < 0.4:  # a company page, and sometimes the very URL a person has
                cells['linkedin_url'] = f'linkedin.com/{rng.choice(["company", "in"])}/p{rng.randrange(25)}'
        else:
            if rng.random() < 0.6:
                cells['email'] = f'p{rng.randrange(25)}@x.test'
            if rng.random() < 0.5:
                cells['linkedin_url'] = rng.choice(
                    [f'linkedin.com/in/p{rng.randrange(25)}'] * 3
                    + [f'linkedin.com/company/p{rng.randrange(25)}', 'linkedin.com/in']
                )
        row = db.execute(
            'INSERT INTO list_rows (list_id, cells, dedupe_key, created_at) VALUES (%s, %s, %s,'
            ' now() - make_interval(secs => %s)) RETURNING id',
            (lid, Jsonb(cells), f'k{n}', 1000 - n),
        ).fetchone()['id']
        ids.append(str(row))
    sql = (MIGRATIONS / MIGRATION).read_text()
    backfill = sql[sql.index('-- Backfill, set-based') :]
    db.execute(backfill)
    set_based = _grouping(db)
    assert set_based[0]  # the data has people in common
    db.execute('DELETE FROM audience_conflicts')
    db.execute('DELETE FROM audience_row_links')
    db.execute('DELETE FROM audience_identities')
    for row in db.execute('SELECT id FROM list_rows ORDER BY created_at, id').fetchall():
        db.execute('SELECT audience_link_row(%s)', (row['id'],))
    assert _grouping(db) == set_based


def _link_on(conn, row_id):
    """What ``link_pending`` does with one row: a pass that lost a race to another turn runs again in a new transaction."""
    for _ in range(3):
        try:
            conn.execute('SELECT audience_link_row(%s)', (row_id,))
            conn.commit()
            return
        except psycopg.errors.SerializationFailure:
            conn.rollback()
    raise AssertionError('three passes lost')


def _connect():
    return psycopg.connect(settings.database_url, row_factory=dict_row)


def _links_of(db, rows):
    return [_identity(db, r) for r in rows]


@needs_db
def test_writers_that_meet_the_same_new_aliases_in_opposite_order_do_not_deadlock(db):
    """The reviewer's repro: T1 writes x then y, T2 writes y then x, in two lists of one project. A writer only marks
    rows, so neither takes an alias lock while it holds row locks; the task links them after the commits."""
    uid = _user(db)
    one, two = _list(db, uid, 'One'), _list(db, uid, 'Two')
    t1, t2 = _connect(), _connect()
    x, y = {'email': 'x@acme.com'}, {'email': 'y@acme.com'}
    errors: list[str] = []
    try:
        (x1,) = add_rows(t1, one, 'person', [x])
        (y2,) = add_rows(t2, two, 'person', [y])

        def second(conn, lid, cells, out):
            try:
                out.extend(add_rows(conn, lid, 'person', [cells]))
                conn.commit()
            except psycopg.errors.DeadlockDetected:
                errors.append('DeadlockDetected')
                conn.rollback()

        y1, x2 = [], []
        threads = [
            threading.Thread(target=second, args=(t1, one, y, y1)),
            threading.Thread(target=second, args=(t2, two, x, x2)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
    finally:
        t1.close()
        t2.close()
    assert errors == []
    _settle(db)
    rows = (x1, y1[0], y2, x2[0])
    ids = _links_of(db, rows)
    assert None not in ids and ids[0] == ids[3] and ids[1] == ids[2] and ids[0] != ids[1]


@needs_db
def test_two_links_that_share_aliases_take_them_in_one_order_and_never_deadlock(db):
    uid = _user(db)
    lists = [_list(db, uid, f'L{n}') for n in range(2)]
    errors: list[str] = []
    for n in range(15):
        cells = {'email': f'pair{n}@acme.com', 'linkedin_url': f'linkedin.com/in/pair{n}'}
        rows = [
            db.execute(
                'INSERT INTO list_rows (list_id, cells, dedupe_key) VALUES (%s, %s, %s) RETURNING id::text AS id',
                (lid, Jsonb(cells), f'k{n}'),
            ).fetchone()['id']
            for lid in lists
        ]
        conns = [_connect(), _connect()]

        def link(conn, rid):
            try:
                _link_on(conn, rid)
            except psycopg.errors.DeadlockDetected:
                errors.append('DeadlockDetected')
                conn.rollback()

        try:
            threads = [threading.Thread(target=link, args=(c, r)) for c, r in zip(conns, rows, strict=True)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(10)
        finally:
            for c in conns:
                c.close()
        assert errors == []
        assert _identity(db, rows[0]) == _identity(db, rows[1]) is not None


@needs_db
def test_a_link_that_loses_a_race_records_the_conflict_and_never_drops_it(db):
    """B reads one identity, then meets a value that A takes before B's insert: B undoes its insert and reads again."""
    uid = _user(db)
    lid = _list(db, uid, 'One')
    holder = _row(db, lid, {'email': 'ada@acme.com', 'full_name': 'Ada'})  # identity IB holds the email
    joined = db.execute(
        'INSERT INTO list_rows (list_id, cells, dedupe_key) VALUES (%s, %s, %s) RETURNING id::text AS id',
        (lid, Jsonb({'email': 'ada@acme.com', 'linkedin_url': LI}), 'joined'),
    ).fetchone()['id']
    ib = _identity(db, holder)
    a = _connect()
    try:
        # A is an open turn that gives another person the profile
        (ia,) = (
            a.execute(
                'SELECT identity_id FROM audience_attach_new(%s, %s, %s, %s, %s, %s)',
                (
                    db.execute('SELECT project_id FROM lists WHERE id = %s', (lid,)).fetchone()['project_id'],
                    'person',
                    'linkedin_url',
                    '',
                    LI,
                    'apollo',
                ),
            )
            .fetchone()
            .values()
        )
        done: list[object] = []
        b = _connect()

        def link_b():
            _link_on(b, joined)
            done.append(True)

        t = threading.Thread(target=link_b)
        t.start()
        t.join(0.5)
        assert t.is_alive()  # B waits on A's value
        a.commit()
        t.join(10)
        b.close()
    finally:
        a.close()
    assert done
    (conflict,) = _conflicts(db)
    assert {str(conflict['identity_a']), str(conflict['identity_b'])} == {str(ia), ib}
    assert _identity(db, joined) is None  # nothing attached to a row that two identities claim


@needs_db
def test_a_merge_waits_for_a_link_in_progress_and_the_two_never_deadlock(client, db):
    _, uid = client
    a, b, conflict = _conflict(db, uid)
    lid = db.execute('SELECT list_id FROM list_rows WHERE id = %s', (a,)).fetchone()['list_id']
    extra = db.execute(
        'INSERT INTO list_rows (list_id, cells, dedupe_key) VALUES (%s, %s, %s) RETURNING id::text AS id',
        (lid, Jsonb({'email': 'ada@acme.com', 'title': 'CEO'}), 'extra'),
    ).fetchone()['id']
    link, merge = _connect(), _connect()
    out: list[object] = []
    try:
        link.execute('SELECT audience_link_row(%s)', (extra,))  # holds a key-share lock on its identity, uncommitted

        def resolve_it():
            try:
                with merge.transaction():
                    out.append(conflicts.resolve(merge, conflict['id'], conflict['project_id'], uid, 'merge'))
            except psycopg.errors.DeadlockDetected:
                out.append('DeadlockDetected')

        t = threading.Thread(target=resolve_it)
        t.start()
        t.join(0.5)
        assert t.is_alive() and not out  # the merge waits for the link
        link.commit()
        t.join(10)
    finally:
        link.close()
        merge.close()
    assert out and out[0] != 'DeadlockDetected' and out[0]['status'] == 'merged'
    assert _identity(db, a) == _identity(db, b) == _identity(db, extra)


@needs_db
def test_a_merge_marks_the_rows_it_left_unlinked_and_does_not_link_them_itself(client, db):
    tc, uid = client
    a, b, conflict = _conflict(db, uid)
    db.execute('DELETE FROM audience_row_links WHERE list_row_id = %s', (b,))
    tc.post(f'/v1/audience/conflicts/{conflict["id"]}/resolve', json={'action': 'merge'})
    assert _identity(db, b) is None
    assert [str(r['list_row_id']) for r in db.execute('SELECT list_row_id FROM audience_link_queue')] == [b]
    _settle(db)
    assert _identity(db, b) == _identity(db, a)


@needs_db
def test_a_company_page_and_a_person_with_one_linkedin_url_each_get_their_own_identity(db):
    uid = _user(db)
    people, companies = _list(db, uid, 'People'), _list(db, uid, 'Companies', 'company')
    person = _row(db, people, {'full_name': 'Ada', 'linkedin_url': 'linkedin.com/in/acme-ada', 'email': 'ada@acme.com'})
    company = _row(
        db, companies, {'name': 'Acme', 'linkedin_url': 'linkedin.com/in/acme-ada', 'domain': 'acme.com'}, 'company'
    )
    assert _identity(db, person) and _identity(db, company) and _identity(db, person) != _identity(db, company)
    assert not _conflicts(db)
    pid = db.execute('SELECT project_id FROM lists WHERE id = %s', (people,)).fetchone()['project_id']
    followed = alias_map(db, pid, [('email', 'ada@acme.com')])
    assert ('domain', 'acme.com') not in followed and followed[('linkedin_url', 'linkedin.com/in/acme-ada')]
    company_followed = alias_map(db, pid, [('domain', 'acme.com')])
    assert ('email', 'ada@acme.com') not in company_followed


@needs_db
def test_the_link_task_links_the_marked_rows_and_a_failing_row_stays_marked(db, monkeypatch):
    monkeypatch.setattr('app.audience.link.get_conn', scoped_conn(db))
    uid = _user(db)
    lid = _list(db, uid, 'One')
    org = member(db, uid).org_id

    def write():
        with scoped_conn(db)() as conn:
            return add_rows(conn, lid, 'person', [{'email': 'ada@acme.com'}, {'email': 'bob@acme.com'}])

    first, second = call_for(org, write)
    assert _identity(db, first) is None  # a writer marks, it does not link
    assert db.execute('SELECT count(*) AS n FROM audience_link_queue').fetchone()['n'] == 2
    calls = []
    real = scoped_conn(db)

    def flaky(timeout=None):
        calls.append(1)
        if len(calls) == 2:  # the first row's transaction
            raise RuntimeError('database went away')
        return real()

    monkeypatch.setattr('app.audience.link.get_conn', flaky)
    assert call_for(org, link_pending) == 1  # the failing row does not stop the other one
    assert db.execute('SELECT count(*) AS n FROM audience_link_queue').fetchone()['n'] == 1
    monkeypatch.setattr('app.audience.link.get_conn', real)
    assert call_for(org, link_pending) == 1
    assert _identity(db, first) and _identity(db, second)
    assert db.execute('SELECT count(*) AS n FROM audience_link_queue').fetchone()['n'] == 0


@needs_db
def test_a_write_that_marks_a_row_again_keeps_it_marked_for_the_link_that_read_the_old_mark(db):
    uid = _user(db)
    rid = _row(db, _list(db, uid, 'One'), {'email': 'ada@acme.com'})
    mark_rows(db, [rid])
    stale = db.execute('SELECT queued_at FROM audience_link_queue').fetchone()['queued_at']
    mark_rows(db, [rid], 'manual')
    gone = db.execute(
        'DELETE FROM audience_link_queue WHERE list_row_id = %s AND queued_at = %s RETURNING 1', (rid, stale)
    ).fetchall()
    assert gone == []  # the link that read the old mark does not delete the newer one
    assert db.execute('SELECT source FROM audience_link_queue').fetchone()['source'] == 'manual'
    mark_rows(db, [rid])
    assert db.execute('SELECT source FROM audience_link_queue').fetchone()['source'] == 'manual'  # not erased


@needs_db
def test_the_beat_picks_each_organization_with_marked_rows_and_links_it_in_its_scope(db, monkeypatch):
    monkeypatch.setattr('app.audience.link.get_conn', scoped_conn(db))
    monkeypatch.setattr('app.audience.beat.get_conn', scoped_conn(db))
    one, two = _user(db), _user(db)
    rows = []
    for u in (one, two):
        lid = _list(db, u, 'L')
        (rid,) = add_rows(db, lid, 'person', [{'email': f'p{len(rows)}@acme.com'}])
        rows.append(rid)
    assert sorted(call_system(SystemReason.DUE_WORK, beat.due_organizations)) == sorted(
        {str(member(db, one).org_id), str(member(db, two).org_id)}
    )
    assert call_system(SystemReason.DUE_WORK, beat.run) == {'organizations': 2, 'linked': 2}
    assert None not in _links_of(db, rows)
    assert call_system(SystemReason.DUE_WORK, beat.due_organizations) == []


@needs_db
def test_the_backfill_of_a_long_chain_of_rows_does_not_scan_once_per_link(db):
    uid = _user(db)
    lid = _list(db, uid, 'Chain')
    db.execute(
        "INSERT INTO list_rows (list_id, cells, dedupe_key, created_at) SELECT %s, jsonb_build_object('email',"
        " 'e' || (n / 2) || '@x.test', 'linkedin_url', 'linkedin.com/in/l' || ((n + 1) / 2)), 'k' || n,"
        ' now() - make_interval(secs => 5000 - n) FROM generate_series(1, 1000) n',
        (lid,),
    )
    # each row shares its email with the row before it and its profile with the row after it: one chain, one person
    sql = (MIGRATIONS / MIGRATION).read_text()
    started = time.monotonic()
    db.execute(sql[sql.index('-- Backfill, set-based') :])
    assert time.monotonic() - started < 15
    assert db.execute('SELECT count(DISTINCT identity_id) AS n FROM audience_row_links').fetchone()['n'] <= 2


@needs_db
def test_a_link_that_waits_too_long_for_another_links_value_gives_up_and_stays_marked(db, monkeypatch):
    monkeypatch.setattr('app.audience.link.get_conn', scoped_conn(db))
    monkeypatch.setattr('app.audience.link.LOCK_TIMEOUT', '300ms')
    uid = _user(db)
    lid = _list(db, uid, 'One')
    org = member(db, uid).org_id
    (rid,) = add_rows(db, lid, 'person', [{'email': 'ada@acme.com'}])
    pid = db.execute('SELECT project_id FROM lists WHERE id = %s', (lid,)).fetchone()['project_id']
    other = _connect()
    try:
        other.execute(
            'SELECT * FROM audience_attach_new(%s, %s, %s, %s, %s, %s)',
            (pid, 'person', 'email', '', 'ada@acme.com', 'x'),
        )
        assert call_for(org, link_pending) == 0  # waits 300 ms for the open turn's value, then gives up
        assert db.execute('SELECT count(*) AS n FROM audience_link_queue').fetchone()['n'] == 1
        other.commit()
    finally:
        other.close()
    assert call_for(org, link_pending) == 1
    assert _identity(db, rid) is not None


def test_the_link_beat_is_registered_and_scheduled():
    from app import worker

    assert 'link_audience_rows' in worker.celery_app.tasks
    assert worker.celery_app.conf.beat_schedule['link-audience-rows']['task'] == 'link_audience_rows'


LINKEDIN_SAMPLES = [
    ('person', 'linkedin.com/in/ada-rossi'),
    ('person', 'uk.linkedin.com/in/ada-rossi'),
    ('person', 'linkedin.com/in'),
    ('person', 'linkedin.com/in/'),
    ('person', 'linkedin.com/company/acme'),
    ('person', 'linkedin.com/school/mit'),
    ('person', 'linkedin.com/pub/ada'),
    ('company', 'linkedin.com/company/acme'),
    ('company', 'linkedin.com/school/mit'),
    ('company', 'linkedin.com/showcase/acme-labs'),
    ('company', 'linkedin.com/company'),
    ('company', 'linkedin.com/in/ada-rossi'),
    ('company', 'notlinkedin.com/company/acme'),
]


@needs_db
@pytest.mark.parametrize(('entity', 'stored'), LINKEDIN_SAMPLES)
def test_sql_and_python_agree_whose_page_a_linkedin_url_is(db, entity, stored):
    sql = db.execute('SELECT audience_linkedin_fits(%s, %s) AS v', (entity, stored)).fetchone()['v']
    assert sql == aliases.linkedin_fits(entity, stored)
    assert (aliases.entity_of('linkedin_url', stored) == 'company') == aliases.linkedin_fits('company', stored)


@needs_db
def test_a_company_page_on_a_person_row_never_joins_people_and_an_opt_out_does_not_reach_a_stranger(db):
    """The reviewer's probe: A and B share a company page, B and C share an email."""
    uid = _user(db)
    pid = project(db, uid)
    one, two, three = (_list(db, uid, n) for n in ('One', 'Two', 'Three'))
    page = 'https://www.linkedin.com/company/acme'
    a = _row(db, one, {'email': 'ada@acme.com', 'linkedin_url': page})
    b = _row(db, two, {'email': 'bob@acme.com', 'linkedin_url': page})
    c = _row(db, three, {'email': 'bob@acme.com'})
    assert _identity(db, a) and _identity(db, b) and _identity(db, a) != _identity(db, b)
    assert _identity(db, b) == _identity(db, c)
    assert not _conflicts(db)
    assert db.execute("SELECT count(*) AS n FROM audience_aliases WHERE kind = 'linkedin_url'").fetchone()['n'] == 0
    db.execute(
        'INSERT INTO exclusions (project_id, user_id, kind, value, origin)'
        " VALUES (%s, %s, 'email', 'ada@acme.com', 'unsubscribed')",
        (pid, uid),
    )
    assert not is_excluded(load_rules(db, pid), {'email': 'bob@acme.com'})


@needs_db
def test_a_linkedin_url_with_no_slug_is_no_alias_and_each_entity_keeps_its_own_page(db):
    uid = _user(db)
    people, companies = _list(db, uid, 'People'), _list(db, uid, 'Companies', 'company')
    urls = (('A', 'https://www.linkedin.com/in/'), ('B', 'linkedin.com/in'))
    bare = [
        db.execute(
            'INSERT INTO list_rows (list_id, cells, dedupe_key) VALUES (%s, %s, %s) RETURNING id::text AS id',
            (people, Jsonb({'full_name': n, 'linkedin_url': u}), f'k{n}'),
        ).fetchone()['id']
        for n, u in urls
    ]
    _link(db, bare)
    assert _links_of(db, bare) == [None, None]
    wrong = _row(
        db, companies, {'name': 'Acme', 'domain': 'acme.com', 'linkedin_url': 'linkedin.com/in/ada'}, 'company'
    )
    assert _identity(db, wrong)
    assert db.execute("SELECT count(*) AS n FROM audience_aliases WHERE kind = 'linkedin_url'").fetchone()['n'] == 0
    page = _row(db, companies, {'name': 'Acme Inc', 'linkedin_url': 'linkedin.com/company/acme'}, 'company')
    assert _identity(db, page)
    pid = db.execute('SELECT project_id FROM lists WHERE id = %s', (people,)).fetchone()['project_id']
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        attach_new_alias(db, pid, 'person', 'linkedin_url', '', 'linkedin.com/company/acme', 'x')


@needs_db
def test_a_row_that_always_fails_is_given_up_after_the_cap_and_a_write_gives_it_another_chance(db, monkeypatch):
    monkeypatch.setattr('app.audience.link.get_conn', scoped_conn(db))
    uid = _user(db)
    org = member(db, uid).org_id
    rid = _row(db, _list(db, uid, 'One'), {'email': 'ada@acme.com'})
    mark_rows(db, [rid])
    calls = []

    def poison(mark):
        calls.append(mark['id'])
        raise RuntimeError('always')

    monkeypatch.setattr(link_module, '_link_once', poison)
    for _ in range(MAX_ATTEMPTS + 2):
        assert call_for(org, link_pending) == 0
    assert len(calls) == MAX_ATTEMPTS  # given up: not tried again
    assert db.execute('SELECT attempts FROM audience_link_queue').fetchone()['attempts'] == MAX_ATTEMPTS
    mark_rows(db, [rid])  # a new write of the row
    assert db.execute('SELECT attempts FROM audience_link_queue').fetchone()['attempts'] == 0
    call_for(org, link_pending)
    assert len(calls) == MAX_ATTEMPTS + 1


@needs_db
def test_the_task_time_limit_ends_the_link_and_is_not_counted_against_a_row(db, monkeypatch):
    monkeypatch.setattr('app.audience.link.get_conn', scoped_conn(db))
    uid = _user(db)
    org = member(db, uid).org_id
    rid = _row(db, _list(db, uid, 'One'), {'email': 'ada@acme.com'})
    mark_rows(db, [rid])

    def late(mark):
        raise SoftTimeLimitExceeded

    monkeypatch.setattr(link_module, '_link_once', late)
    with pytest.raises(SoftTimeLimitExceeded):
        call_for(org, link_pending)
    assert db.execute('SELECT attempts FROM audience_link_queue').fetchone()['attempts'] == 0


@needs_db
def test_a_link_stops_starting_rows_at_the_deadline(db, monkeypatch):
    monkeypatch.setattr('app.audience.link.get_conn', scoped_conn(db))
    uid = _user(db)
    org = member(db, uid).org_id
    rid = _row(db, _list(db, uid, 'One'), {'email': 'ada@acme.com'})
    mark_rows(db, [rid])
    assert call_for(org, link_pending, deadline=time.monotonic() - 1) == 0
    assert db.execute('SELECT count(*) AS n FROM audience_link_queue').fetchone()['n'] == 1
    assert call_for(org, link_pending, deadline=time.monotonic() + 60) == 1


def _three_identities(db, uid):
    lid = _list(db, uid, 'One')
    rows = [_row(db, lid, {'email': f'p{n}@acme.com'}) for n in range(3)]
    pid = db.execute('SELECT project_id FROM lists WHERE id = %s', (lid,)).fetchone()['project_id']
    return rows, [_identity(db, r) for r in rows], pid


def _raise_conflict(db, org, pid, x, y):
    db.execute("SELECT audience_conflict(%s, %s, %s, %s, NULL, '[]')", (org, pid, x, y))
    return db.execute(
        'SELECT id FROM audience_conflicts WHERE identity_a = least(%s::uuid, %s::uuid)'
        ' AND identity_b = greatest(%s::uuid, %s::uuid)',
        (x, y, x, y),
    ).fetchone()['id']


@needs_db
def test_resolving_a_conflict_that_names_a_folded_identity_merges_into_the_survivor(db):
    uid = _user(db)
    rows, ids, pid = _three_identities(db, uid)
    org = member(db, uid).org_id
    first = conflicts.resolve(db, _raise_conflict(db, org, pid, ids[0], ids[1]), pid, uid, 'merge')['identity_id']
    folded = ({ids[0], ids[1]} - {first}).pop()
    # What a link that read before the merge leaves: a conflict between the folded identity and a third one.
    stale = _raise_conflict(db, org, pid, folded, ids[2])
    out = conflicts.resolve(db, stale, pid, uid, 'merge')
    live = [r['id'] for r in db.execute('SELECT id::text AS id FROM audience_identities WHERE merged_into IS NULL')]
    assert live == [out['identity_id']]
    held = {r['i'] for r in db.execute('SELECT identity_id::text AS i FROM audience_aliases')}
    assert held == {out['identity_id']}  # no alias moved onto the tombstone
    assert len({_identity(db, r) for r in rows}) == 1


@needs_db
def test_a_conflict_whose_two_sides_were_folded_into_one_closes_with_nothing_to_move(db):
    uid = _user(db)
    _, ids, pid = _three_identities(db, uid)
    org = member(db, uid).org_id
    first = _raise_conflict(db, org, pid, ids[0], ids[1])
    second = _raise_conflict(db, org, pid, ids[0], ids[2])
    conflicts.resolve(db, first, pid, uid, 'merge')
    conflicts.resolve(db, second, pid, uid, 'merge')
    third = _raise_conflict(db, org, pid, ids[1], ids[2])  # both already folded into one identity
    out = conflicts.resolve(db, third, pid, uid, 'merge')
    assert out['status'] == 'merged'
    assert len(db.execute('SELECT 1 FROM audience_identities WHERE merged_into IS NULL').fetchall()) == 1


@needs_db
def test_a_link_takes_the_identities_it_reads_in_id_order_so_a_merge_cannot_cycle_with_it(db):
    uid = _user(db)
    lid = _list(db, uid, 'One')
    p1 = _row(db, lid, {'email': 'ada@acme.com'})
    p2 = _row(db, lid, {'linkedin_url': LI})
    both = db.execute(
        'INSERT INTO list_rows (list_id, cells, dedupe_key) VALUES (%s, %s, %s) RETURNING id::text AS id',
        (lid, Jsonb({'email': 'ada@acme.com', 'linkedin_url': LI}), 'both'),
    ).fetchone()['id']
    low, high = sorted([_identity(db, p1), _identity(db, p2)])
    merge, link = _connect(), _connect()
    out: list[object] = []

    def run_link():
        _link_on(link, both)
        out.append('linked')

    try:
        merge.execute('SELECT 1 FROM audience_identities WHERE id = %s FOR UPDATE', (low,))  # a merge has the low one
        t = threading.Thread(target=run_link)
        t.start()
        t.join(0.5)
        assert t.is_alive()  # the link waits for the low identity, holding nothing on the high one
        merge.execute("SET lock_timeout = '2s'")
        merge.execute('SELECT 1 FROM audience_identities WHERE id = %s FOR UPDATE', (high,))  # the merge goes on
        merge.commit()
        t.join(10)
    finally:
        link.close()
        merge.close()
    assert out == ['linked'] and len(_conflicts(db)) == 1


@needs_db
def test_rows_given_up_never_hold_their_organization_first_or_starve_the_others(db, monkeypatch):
    monkeypatch.setattr('app.audience.beat.get_conn', scoped_conn(db))
    for n in range(beat.ORGANIZATIONS + 2):
        add_rows(db, _list(db, _user(db), f'L{n}'), 'person', [{'email': f'p{n}@acme.com'}])
    fresh_uid = _user(db)
    add_rows(db, _list(db, fresh_uid, 'Fresh'), 'person', [{'email': 'fresh@acme.com'}])
    fresh = str(member(db, fresh_uid).org_id)
    # every other organization's only mark is older and given up, as the fifth failure leaves it
    db.execute(
        "UPDATE audience_link_queue SET attempts = %s, queued_at = now() - interval '1 hour'"
        ' WHERE organization_id <> %s',
        (link_module.MAX_ATTEMPTS, fresh),
    )
    assert call_system(SystemReason.DUE_WORK, beat.due_organizations) == [fresh]


@needs_db
def test_an_organization_with_many_old_marks_takes_one_place_in_the_pick(db, monkeypatch):
    monkeypatch.setattr('app.audience.beat.get_conn', scoped_conn(db))
    busy_uid = _user(db)
    add_rows(db, _list(db, busy_uid, 'Big import'), 'person', [{'email': f'p{n}@acme.com'} for n in range(30)])
    busy = str(member(db, busy_uid).org_id)
    db.execute("UPDATE audience_link_queue SET queued_at = now() - interval '1 hour'")
    fresh_uid = _user(db)
    add_rows(db, _list(db, fresh_uid, 'Fresh'), 'person', [{'email': 'fresh@acme.com'}])
    fresh = str(member(db, fresh_uid).org_id)
    monkeypatch.setattr(beat, 'ORGANIZATIONS', 2)
    assert call_system(SystemReason.DUE_WORK, beat.due_organizations) == [busy, fresh]


@needs_db
def test_the_beat_reads_the_marks_that_are_not_given_up_from_a_partial_index(db):
    assert (
        f'WHERE (attempts < {link_module.MAX_ATTEMPTS})'
        in db.execute("SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_audience_link_queue_due'").fetchone()[
            'indexdef'
        ]
    )
    db.execute('SET enable_seqscan = off')
    plan = db.execute(f'EXPLAIN {beat.DUE_SQL}', (beat.ORGANIZATIONS,)).fetchall()
    assert 'idx_audience_link_queue_due' in '\n'.join(r['QUERY PLAN'] for r in plan)


@needs_db
def test_a_link_that_meets_a_merge_reads_again_and_joins_the_survivor(db):
    """The link read an identity that a merge is folding: the pass raises serialization_failure once, then the row
    links to the survivor."""
    uid = _user(db)
    lid = _list(db, uid, 'One')
    org = member(db, uid).org_id
    a = _row(db, lid, {'email': 'ada@acme.com'})
    b = _row(db, lid, {'email': 'bob@acme.com'})
    loser, winner = _identity(db, a), _identity(db, b)
    pid = db.execute('SELECT project_id FROM lists WHERE id = %s', (lid,)).fetchone()['project_id']
    cid = _raise_conflict(db, org, pid, loser, winner)
    late = db.execute(
        'INSERT INTO list_rows (list_id, cells, dedupe_key) VALUES (%s, %s, %s) RETURNING id::text AS id',
        (lid, Jsonb({'email': 'ada@acme.com', 'title': 'CEO'}), 'late'),
    ).fetchone()['id']
    merge, link = _connect(), _connect()
    failures: list[str] = []
    out: list[str] = []

    def run_link():
        for _ in range(3):
            try:
                link.execute('SELECT audience_link_row(%s)', (late,))
                link.commit()
                out.append('linked')
                return
            except psycopg.errors.SerializationFailure:
                failures.append('SerializationFailure')
                link.rollback()

    try:
        conflicts.resolve(merge, cid, pid, uid, 'merge', into=winner)  # the merge holds both identities, uncommitted
        t = threading.Thread(target=run_link)
        t.start()
        t.join(0.5)
        assert t.is_alive()  # the link read the loser and waits for the merge
        merge.commit()
        t.join(10)
    finally:
        link.close()
        merge.close()
    assert failures == ['SerializationFailure'] and out == ['linked']
    assert _identity(db, late) == winner
    assert not db.execute('SELECT 1 FROM audience_conflicts WHERE status = %s', ('open',)).fetchall()
