from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import NamedTuple, TypedDict

from psycopg.types.json import Jsonb

from app.outreach import send_states, variants
from app.outreach.fields import load_inputs, missing_in_steps, parse_fields, resolve

_TARGETS = """
SELECT id::text AS id, status, variant, first_name, last_name, company_name, role, email
  FROM outreach_campaign_targets WHERE campaign_id = %s AND id = ANY(%s::uuid[])"""

_WRITE = """
UPDATE outreach_campaign_targets t
   SET personalization = s.snapshot, frozen_at = now(),
       status = CASE WHEN t.status = 'draft' AND s.complete THEN 'active' ELSE t.status END,
       variant = CASE WHEN t.variant IS NULL AND t.status = 'draft' AND s.complete THEN s.variant ELSE t.variant END
  FROM jsonb_to_recordset(%(snapshots)s) AS s(id uuid, snapshot jsonb, complete boolean, variant text)
 WHERE t.id = s.id AND t.campaign_id = %(campaign)s
   AND (t.status = 'draft' OR (s.complete AND t.status IN ('active', 'paused')))
RETURNING t.id::text AS id, t.status"""

# From the campaign itself, not the caller's row: callers select different columns.
_ALLOCATION = """
SELECT c.b_percent, c.allocation_paused_at,
       count(*) FILTER (WHERE t.variant = 'a') AS a, count(*) FILTER (WHERE t.variant = 'b') AS b
  FROM outreach_campaigns c
  LEFT JOIN outreach_campaign_targets t ON t.campaign_id = c.id AND t.variant IS NOT NULL
 WHERE c.id = %s
 GROUP BY c.id"""

WAITING_FOR_A_VALUE = "status = 'draft' AND personalization IS NOT NULL"


class Snapshot(TypedDict):
    values: dict[str, str]
    sources: dict[str, dict]
    missing: list[str]


class Waiting(TypedDict, total=False):
    waiting: int
    missing: list[str]


class Frozen(NamedTuple):
    active: list[str]
    renewed: list[str]
    waiting: dict[str, list[str]]
    kept: list[str]

    def why_waiting(self) -> Waiting:
        if not self.waiting:
            return {}
        return {
            'waiting': len(self.waiting),
            'missing': sorted({t for tokens in self.waiting.values() for t in tokens}),
        }


@contextmanager
def freezing(conn) -> Iterator[None]:
    with conn.transaction():
        conn.execute('SET LOCAL zenbree.freezing = on')
        yield
        conn.execute('SET LOCAL zenbree.freezing = off')


def freeze(conn, campaign: dict, target_ids: list[str]) -> Frozen:
    """Freeze each person's values; a complete draft becomes active and, while the A/B test allocates, gets its copy
    variant. The caller holds the project lock, which keeps the split of a campaign's joins exact."""
    if not target_ids:
        return Frozen([], [], {}, [])
    fields = parse_fields(campaign['fields'])
    inputs = load_inputs(conn, campaign, target_ids)
    split = conn.execute(_ALLOCATION, (campaign['id'],)).fetchone()
    allocating = variants.allocating({**campaign, 'allocation_paused_at': split['allocation_paused_at']})
    snapshots, joining = [], []
    for t in conn.execute(_TARGETS, (campaign['id'], target_ids)).fetchall():
        resolved = resolve(fields, t, inputs.get(t['id']))
        joins = allocating and t['status'] == 'draft' and t['variant'] is None
        copy = variants.copy_to_fill(campaign['steps'], t['variant'], joins_test=joins)
        missing = missing_in_steps(resolved.missing, copy)
        snapshot = Snapshot(values=resolved.values, sources=resolved.sources, missing=missing)
        snapshots.append({'id': t['id'], 'snapshot': snapshot, 'complete': not missing, 'was': t['status']})
        if joins and not missing:
            joining.append(t['id'])
    chosen = variants.assign(variants.Split(split['a'], split['b']), joining, split['b_percent'], campaign['id'])
    for s in snapshots:
        s['variant'] = chosen.get(s['id'])
    with freezing(conn):
        rows = conn.execute(_WRITE, {'snapshots': Jsonb(snapshots), 'campaign': campaign['id']}).fetchall()
    written = {r['id'] for r in rows}
    frozen = Frozen([], [], {}, [])
    for s in snapshots:
        if s['id'] not in written:
            if s['was'] in ('active', 'paused'):
                frozen.kept.append(s['id'])
        elif s['was'] != 'draft':
            frozen.renewed.append(s['id'])
        elif s['complete']:
            frozen.active.append(s['id'])
        else:
            frozen.waiting[s['id']] = s['snapshot']['missing']
    return frozen


def frozen_values(target: dict) -> dict[str, str]:
    snapshot = target.get('personalization')
    return snapshot['values'] if snapshot else _values_before_freeze(target)


def _values_before_freeze(target: dict) -> dict[str, str]:
    return resolve([], target, None).values


def drop_unsent(conn, target_ids: list[str]) -> int:
    """Postgres READ COMMITTED: a DELETE that waits on a claim's row lock checks its WHERE again after the claim
    commits, so a send that the claim moved out of these states stays."""
    return conn.execute(
        'DELETE FROM outreach_campaign_sends'
        f' WHERE target_id = ANY(%s::uuid[]) AND status IN ({", ".join(map(repr, sorted(send_states.UNTAKEN)))})',
        (target_ids,),
    ).rowcount
