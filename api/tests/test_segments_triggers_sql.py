"""The marks that writes leave for the segments beat (#938): which writes mark whom, and what a project with no
published segment pays."""

from __future__ import annotations

import pytest

from tests.test_agent import db as db
from tests.test_agent import needs_db
from tests.test_audience_sql import _identity, _row
from tests.test_scope_sql import scoped
from tests.test_segments_sql import MARCO_RULE, people, segment

pytestmark = needs_db


def marks(db) -> list[dict]:
    return db.execute(
        'SELECT organization_id::text, project_id::text, list_row_id::text, identity_id::text FROM segment_dirty'
        ' ORDER BY id'
    ).fetchall()


def marked_rows(db) -> set[str]:
    return {m['list_row_id'] for m in marks(db) if m['list_row_id']}


def marked_identities(db) -> set[str]:
    return {m['identity_id'] for m in marks(db) if m['identity_id']}


def test_a_project_with_no_published_segment_gets_no_marks(db):
    p = people(db)
    segment(db, p, MARCO_RULE, published=False)
    other = people(db)
    segment(db, other, MARCO_RULE)
    db.execute(
        'UPDATE list_rows SET cells = cells || \'{"title": "CTO"}\', relevance_score = 10 WHERE list_id = ANY(%s)',
        ([p['a'], p['b']],),
    )
    db.execute('DELETE FROM list_rows WHERE id = %s', (p['giulia'],))
    _row(db, p['a'], {'full_name': 'Ada', 'email': 'ada@acme.verify.test'})
    db.execute('UPDATE lists SET deleted_at = now() WHERE id = %s', (p['b'],))
    assert other['pid'] != p['pid']
    assert marks(db) == []


def test_a_bulk_update_marks_every_changed_row_in_one_trigger_call(db):
    p = people(db)
    segment(db, p, MARCO_RULE)
    db.execute("SET track_functions = 'pl'")
    with db.transaction():
        db.execute(
            'UPDATE list_rows SET relevance_score = relevance_score + 1 WHERE list_id = ANY(%s)', ([p['a'], p['b']],)
        )
        calls = db.execute(
            "SELECT calls FROM pg_stat_xact_user_functions WHERE funcname = 'segment_mark_rows'"
        ).fetchone()['calls']
    assert calls == 1
    assert marked_rows(db) == {p['marco_a'], p['marco_b'], p['giulia']}
    assert {(m['organization_id'], m['project_id']) for m in marks(db)} == {(p['org'], str(p['pid']))}


@pytest.mark.parametrize(
    ('change', 'marked'),
    [
        ('cells = cells || \'{"title": "CTO"}\'', True),
        ('relevance_score = 40', True),
        ('excluded = true', True),
        ('cell_meta = cell_meta || \'{"title": {"status": "failed"}}\'', True),
        ("rating = 'good'", False),
        ('cells = cells, relevance_score = relevance_score', False),
    ],
)
def test_only_a_change_the_judge_reads_marks_a_row(db, change, marked):
    p = people(db)
    segment(db, p, MARCO_RULE)
    db.execute(f'UPDATE list_rows SET {change} WHERE id = %s', (p['giulia'],))
    assert marked_rows(db) == ({p['giulia']} if marked else set())


def test_a_link_change_marks_the_identity_that_lost_the_row_and_the_one_that_gained_it(db):
    p = people(db)
    segment(db, p, MARCO_RULE)
    other = db.execute(
        "INSERT INTO audience_identities (organization_id, project_id, entity) VALUES (%s, %s, 'person') RETURNING id",
        (p['org'], p['pid']),
    ).fetchone()['id']
    db.execute('UPDATE audience_row_links SET identity_id = %s WHERE list_row_id = %s', (other, p['marco_b']))
    assert marked_identities(db) == {p['marco'], str(other)}


def test_a_new_link_and_a_deleted_row_mark_their_identity(db):
    p = people(db)
    segment(db, p, MARCO_RULE)
    ada = _row(db, p['a'], {'full_name': 'Ada', 'email': 'ada@acme.verify.test'})
    assert marked_identities(db) == {_identity(db, ada)}
    db.execute('DELETE FROM segment_dirty')
    giulia = _identity(db, p['giulia'])
    db.execute('DELETE FROM list_rows WHERE id = %s', (p['giulia'],))
    assert marked_identities(db) == {giulia}


def test_deleting_or_restoring_a_list_marks_every_person_with_a_row_in_it(db):
    p = people(db)
    segment(db, p, MARCO_RULE)
    everyone = {p['marco'], _identity(db, p['giulia'])}
    db.execute('UPDATE lists SET deleted_at = now() WHERE id = %s', (p['a'],))
    assert marked_identities(db) == everyone
    db.execute('DELETE FROM segment_dirty')
    db.execute("UPDATE lists SET name = 'A2' WHERE id = %s", (p['a'],))
    assert marks(db) == []
    db.execute('UPDATE lists SET deleted_at = NULL WHERE id = %s', (p['a'],))
    assert marked_identities(db) == everyone


def test_a_member_writes_marks_in_their_own_organization(db):
    p = people(db)
    segment(db, p, MARCO_RULE)
    with scoped(db, member=(p['org'], p['uid'])) as c:
        c.execute('UPDATE list_rows SET cells = cells || \'{"title": "CTO"}\' WHERE id = %s', (p['giulia'],))
    assert [(m['organization_id'], m['list_row_id']) for m in marks(db)] == [(p['org'], p['giulia'])]


def test_a_member_deletes_a_project_with_a_published_segment_and_linked_rows(db):
    p = people(db)
    seg = segment(db, p, MARCO_RULE)
    db.execute(
        'INSERT INTO segment_members (segment_id, identity_id, organization_id, list_row_id, episode)'
        ' VALUES (%s, %s, %s, %s, 1)',
        (seg['id'], p['marco'], p['org'], p['marco_a']),
    )
    with scoped(db, member=(p['org'], p['uid'])) as c:
        assert c.execute('DELETE FROM projects WHERE id = %s', (p['pid'],)).rowcount == 1
    assert db.execute('SELECT count(*) AS n FROM list_rows WHERE id = %s', (p['marco_a'],)).fetchone()['n'] == 0


def test_the_pause_switch_is_listed_and_off(db):
    row = db.execute("SELECT value FROM settings WHERE key = 'segments_evaluation_paused'").fetchone()
    assert row['value'] is False
