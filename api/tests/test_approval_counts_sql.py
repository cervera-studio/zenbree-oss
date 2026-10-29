import pytest

from app.home.overview import awaiting_approval
from app.outreach import approval_queue, holds
from tests.conftest import project
from tests.test_holds_sql import ANN, lead, org_of, person, second_project, stop
from tests.test_holds_sql import db as db
from tests.test_tools_sql import _user

CHANNELS = ('email', 'linkedin_connect', 'hubspot_task')
PEOPLE = [
    ('ann@acme.test', None),
    (None, 'https://www.linkedin.com/in/ann/?trk=x'),
    ('cat@acme.test', 'linkedin.com/in/ann'),
    ('info@acme.test', None),
    ('bob@acme.test', ''),
    ('dan@acme.test', 'linkedin.com/in/dan'),
    ('eve@acme.test', None),
    ('fay@acme.test', None),
    ('zed@acme.test', None),
    (None, 'linkedin.com/in/zed'),
]


def _campaign(c, uid, pid, channel: str, people: list[tuple[str | None, str | None]]) -> str:
    cid = c.execute(
        'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, steps,'
        " approve_before_sending) VALUES (%s, %s, %s, 'C', 'active', %s::jsonb, true) RETURNING id",
        (org_of(c, pid), pid, uid, f'[{{"channel": "{channel}"}}]'),
    ).fetchone()['id']
    for n, (email, linkedin) in enumerate(people):
        tid = c.execute(
            'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, linkedin_url, status, next_step)'
            " VALUES (%s, %s, %s, %s, 'active', 1) RETURNING id",
            (cid, uid, email, linkedin or ''),
        ).fetchone()['id']
        c.execute(
            'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, recipient,'
            " idempotency_key, status, subject, body) VALUES (%s, %s, %s, 1, %s, 'r', %s, 'awaiting_approval', 's', 'b')",
            (cid, uid, tid, channel, f'{cid}-{n}'),
        )
    return str(cid)


@pytest.mark.parametrize('channel', CHANNELS)
def test_the_counts_hold_exactly_the_people_the_hold_keeps(db, channel):
    uid = _user(db)
    pid, other = project(db, uid), second_project(db, uid)
    person(db, pid, *ANN)
    person(db, pid, ('email', 'cat@acme.test'), ('email', 'cathy@acme.test'))
    person(db, pid, ('email', 'dan@acme.test'), ('linkedin_url', 'linkedin.com/in/dan'))
    lead(db, pid, 'a.smith@acme.test')
    stop(db, pid, 'info@acme.test')
    stop(db, pid, 'bob@acme.test', 'confirmed')
    stop(db, pid, 'dan@acme.test', 'failed')
    lead(db, pid, 'eve@acme.test', 'skipped')
    lead(db, pid, 'eve@acme.test', 'pending')
    stop(db, other, 'fay@acme.test')
    campaigns = {_campaign(db, uid, pid, channel, [p]): p for p in PEOPLE}

    counted = {str(k): v for k, v in approval_queue.counts(db, pid).items()}
    expected = {
        cid: (0, 1) if holds.hold(db, pid, channel, email, linkedin) else (1, 0)
        for cid, (email, linkedin) in campaigns.items()
    }
    assert counted == expected
    held = sum(n.held for n in counted.values())
    assert held == {'email': 6, 'linkedin_connect': 3, 'hubspot_task': 3}[channel]


def test_the_counts_read_the_holds_a_fixed_number_of_times_whatever_the_waiting_messages(db):
    uid = _user(db)
    pid = project(db, uid)
    for n in range(20):
        person(db, pid, ('email', f'p{n}@acme.test'), ('linkedin_url', f'linkedin.com/in/p{n}'))
        lead(db, pid, f'p{n}@acme.test')
    stop(db, pid, 'p0@acme.test')
    hold_tables = ('outreach_stops', 'instantly_leads', 'audience_aliases')

    def scans(people: int) -> int:
        _campaign(db, uid, pid, 'email', [(f'w{people}x{n}@acme.test', None) for n in range(people)])
        with db.transaction():
            before = _scans(db, hold_tables)
            awaiting_approval(db, org_of(db, pid), pid)
            return _scans(db, hold_tables) - before

    assert scans(5) == scans(50)


def _scans(c, tables) -> int:
    return c.execute(
        'SELECT sum(seq_scan + coalesce(idx_scan, 0))::int AS n FROM pg_stat_xact_user_tables WHERE relname = ANY(%s)',
        (list(tables),),
    ).fetchone()['n']
