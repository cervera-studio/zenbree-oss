"""Ambiguous identity mappings and their explicit resolution (#937).

A conflict is a pair of identities that one row or provider id ties together. Code never merges them. A person
either folds one into the other (``merge``: aliases and rows move, the folded identity stays as a tombstone) or says
they are two (``keep_apart``: the pair stays recorded and is not raised again).
"""

from __future__ import annotations

from typing import Literal

from app.audience.link import mark_rows
from app.core.errors_http import api_error

Action = Literal['merge', 'keep_apart']


def _pair(conn, conflict_id, project_id, lock: str = '') -> dict:
    row = conn.execute(
        f'SELECT * FROM audience_conflicts WHERE id = %s AND project_id = %s{lock}', (conflict_id, project_id)
    ).fetchone()
    if not row:
        raise api_error(404, 'AUDIENCE.CONFLICT_NOT_FOUND', 'conflict not found')
    return row


def list_conflicts(conn, project_id, status: str = 'open', cursor: int = 0, limit: int = 50) -> list[dict]:
    """A page of the project's conflicts after ``cursor`` (``seq``), each with, per side, its aliases and the count
    of its rows. A caller asks for ``limit`` and reads ``seq`` of the last one as the next cursor."""
    rows = conn.execute(
        'SELECT c.id, c.seq, c.status, c.evidence, c.created_at, c.resolved_at, c.list_row_id, a.entity,'
        ' c.identity_a::text AS identity_a, c.identity_b::text AS identity_b'
        ' FROM audience_conflicts c JOIN audience_identities a ON a.id = c.identity_a'
        ' WHERE c.project_id = %s AND c.status = %s AND c.seq > %s ORDER BY c.seq LIMIT %s',
        (project_id, status, cursor, limit),
    ).fetchall()
    ids = sorted({i for r in rows for i in (r['identity_a'], r['identity_b'])})
    aliases: dict[str, list] = {i: [] for i in ids}
    for a in conn.execute(
        'SELECT identity_id::text AS identity_id, kind, namespace, value, source, observed_at FROM audience_aliases'
        ' WHERE identity_id = ANY(%s::uuid[]) ORDER BY observed_at, id',
        (ids,),
    ):
        aliases[a['identity_id']].append({k: a[k] for k in ('kind', 'namespace', 'value', 'source', 'observed_at')})
    counts = {
        r['identity_id']: r['n']
        for r in conn.execute(
            'SELECT identity_id::text AS identity_id, count(*) AS n FROM audience_row_links'
            ' WHERE identity_id = ANY(%s::uuid[]) GROUP BY identity_id',
            (ids,),
        )
    }
    return [
        {
            **r,
            'identities': [
                {'id': i, 'aliases': aliases[i], 'rows': counts.get(i, 0)} for i in (r['identity_a'], r['identity_b'])
            ],
        }
        for r in rows
    ]


def resolve(conn, conflict_id, project_id, user_id, action: Action, into=None) -> dict:
    """Close an open conflict. ``merge`` folds the other identity into ``into`` (one of the two; the older when
    absent). Two callers that resolve at once take turns on the identities' row locks."""
    c = _pair(conn, conflict_id, project_id)
    a, b = _lock_live(conn, c)
    c = _pair(conn, conflict_id, project_id, ' FOR UPDATE')
    if c['status'] != 'open':
        raise api_error(409, 'AUDIENCE.CONFLICT_CLOSED', 'conflict already resolved')
    if action == 'keep_apart':
        _close(conn, conflict_id, 'kept_apart', user_id)
        return {'status': 'kept_apart', 'identity_id': None}
    if a == b:  # another merge folded one into the other since the conflict was raised: nothing left to merge
        _close(conn, conflict_id, 'merged', user_id)
        return {'status': 'merged', 'identity_id': a}
    if into is not None and str(into) not in (a, b):
        raise api_error(422, 'AUDIENCE.CONFLICT_SIDE', 'into must be one of the two identities')
    winner = str(into) if into else _older(conn, a, b)
    loser = b if winner == a else a
    _fold(conn, c, winner, loser)
    _close(conn, conflict_id, 'merged', user_id)
    return {'status': 'merged', 'identity_id': winner}


def _lock_live(conn, c: dict) -> tuple[str, str]:
    """The two identities of the conflict as they stand now, locked before anything moves. The pair that the conflict
    names is locked first, in id order (a link takes its identities in id order, so the two cannot wait on each other
    in a cycle). A conflict can name one that a merge folded into another, because the link that raised it read before
    the merge: its aliases live on the survivor, so the survivors are locked too. That second step is the only one out
    of id order; it needs a merge that landed after the conflict, and a deadlock there ends the request, which the user
    repeats."""
    pair = (str(c['identity_a']), str(c['identity_b']))
    lock = 'SELECT 1 FROM audience_identities WHERE id = ANY(%s::uuid[]) ORDER BY id FOR UPDATE'
    conn.execute(lock, (list(pair),))
    for _ in range(5):
        live = tuple(conn.execute('SELECT audience_live(%s::uuid)::text AS id', (i,)).fetchone()['id'] for i in pair)
        if live == pair:
            return live
        conn.execute(lock, (list(live),))
        pair = live
    raise api_error(409, 'AUDIENCE.CONFLICT_CLOSED', 'conflict is being resolved')


def _older(conn, a: str, b: str) -> str:
    row = conn.execute(
        'SELECT id::text AS id FROM audience_identities WHERE id = ANY(%s::uuid[]) ORDER BY created_at, id LIMIT 1',
        ([a, b],),
    ).fetchone()
    return row['id']


def _close(conn, conflict_id, status: str, user_id) -> None:
    conn.execute(
        'UPDATE audience_conflicts SET status = %s, resolved_by = %s, resolved_at = now() WHERE id = %s',
        (status, user_id, conflict_id),
    )


def _fold(conn, c: dict, winner: str, loser: str) -> None:
    conn.execute('UPDATE audience_aliases SET identity_id = %s WHERE identity_id = %s', (winner, loser))
    conn.execute('UPDATE audience_row_links SET identity_id = %s WHERE identity_id = %s', (winner, loser))
    conn.execute('UPDATE audience_identities SET merged_into = %s WHERE id = %s', (winner, loser))
    # Another conflict that named the folded identity now names the winner.
    others = conn.execute(
        'SELECT * FROM audience_conflicts WHERE id <> %s AND (identity_a = %s OR identity_b = %s) FOR UPDATE',
        (c['id'], loser, loser),
    ).fetchall()
    for o in others:
        other = str(o['identity_b'] if str(o['identity_a']) == loser else o['identity_a'])
        conn.execute('DELETE FROM audience_conflicts WHERE id = %s', (o['id'],))
        if other != winner:
            conn.execute(
                'INSERT INTO audience_conflicts (organization_id, project_id, identity_a, identity_b, list_row_id,'
                ' evidence, status, resolved_by, resolved_at, created_at) VALUES (%s, %s, least(%s::uuid, %s::uuid),'
                ' greatest(%s::uuid, %s::uuid), %s, %s, %s, %s, %s, %s) ON CONFLICT (identity_a, identity_b) DO NOTHING',
                (
                    o['organization_id'],
                    o['project_id'],
                    winner,
                    other,
                    winner,
                    other,
                    o['list_row_id'],
                    o['evidence'],
                    o['status'],
                    o['resolved_by'],
                    o['resolved_at'],
                    o['created_at'],
                ),
            )
    # A row the conflict left unlinked joins the winner, and its other aliases attach now.
    rows = [r['list_row_id'] for r in [c, *others] if r['list_row_id']]
    if rows:
        mark_rows(conn, rows)
