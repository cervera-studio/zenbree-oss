from __future__ import annotations

from app.core.migrate import MIGRATIONS
from app.segments.entries import entries_after
from tests.test_agent import db as db
from tests.test_agent import needs_db
from tests.test_segments_sql import MARCO_RULE, people, run, segment

LEDGER = '20261021141152_segment_transitions.sql'


def _schema_before_the_ledger(db) -> None:
    db.execute('DROP TABLE segment_transitions')


@needs_db
def test_each_live_member_gets_one_silent_join_at_its_episode_and_the_next_apply_records_nothing(db):
    _schema_before_the_ledger(db)
    p = people(db)
    seg = segment(db, p, MARCO_RULE)
    other = segment(db, p, MARCO_RULE)
    db.execute('UPDATE segments SET version = 3 WHERE id = %s', (other['id'],))
    giulia = db.execute(
        'SELECT identity_id::text AS id FROM audience_row_links WHERE list_row_id = %s', (p['giulia'],)
    ).fetchone()['id']
    members = [
        (seg['id'], p['marco'], p['marco_a'], 2, None),
        (seg['id'], giulia, p['giulia'], 1, 'now()'),
        (other['id'], p['marco'], p['marco_a'], 1, None),
        (other['id'], giulia, None, 1, None),
    ]
    for segment_id, identity, row, episode, exited in members:
        db.execute(
            'INSERT INTO segment_members (segment_id, identity_id, organization_id, list_row_id, episode, entered_at,'
            f" exited_at) VALUES (%s, %s, %s, %s, %s, '2026-10-20 12:00+00', {exited or 'NULL'})",
            (segment_id, identity, p['org'], row, episode),
        )

    db.execute((MIGRATIONS / LEDGER).read_text())

    ledger = db.execute(
        'SELECT segment_id, identity_id::text, kind, episode, list_row_id::text, segment_version, created_at::text'
        ' FROM segment_transitions ORDER BY segment_id, identity_id'
    ).fetchall()
    expected = sorted(
        [
            (seg['id'], p['marco'], 'baseline', 2, str(p['marco_a']), 1, '2026-10-20 12:00:00+00'),
            (other['id'], p['marco'], 'baseline', 1, str(p['marco_a']), 3, '2026-10-20 12:00:00+00'),
            (other['id'], giulia, 'baseline', 1, None, 3, '2026-10-20 12:00:00+00'),
        ]
    )
    assert [tuple(r.values()) for r in ledger] == expected

    seq = db.execute('SELECT max(seq) AS seq FROM segment_transitions').fetchone()['seq']
    run(db, {**seg, 'version': 1})
    after = db.execute(
        'SELECT identity_id::text, kind, episode FROM segment_transitions WHERE seq > %s', (seq,)
    ).fetchall()
    assert [tuple(r.values()) for r in after] == [(giulia, 'enter', 2)]
    assert [(str(e.identity_id), e.episode) for e in entries_after(db, seg['id'], 0, 10)] == [(giulia, 2)]
