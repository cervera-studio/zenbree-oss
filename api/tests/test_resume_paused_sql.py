"""An out-of-office pause ends (#931): ``holds.resume_paused`` against real SQL. Needs TEST_DATABASE_URL."""

from datetime import timedelta

from app.outreach import holds
from tests import test_dispatcher_sql
from tests.conftest import each_org, project
from tests.test_dispatcher_sql import NOW, _campaign
from tests.test_holds_sql import ANN, person, second_project, stop
from tests.test_tools_sql import _user

db = test_dispatcher_sql.db


def _paused(c, uid, email='ann@acme.test', linkedin='') -> str:
    cid = _campaign(c, uid, emails=(email,))
    c.execute(
        "UPDATE outreach_campaign_targets SET status = 'paused', paused_until = %s, linkedin_url = %s"
        ' WHERE campaign_id = %s',
        (NOW - timedelta(minutes=1), linkedin, cid),
    )
    return cid


def _elsewhere(c, uid, status, email='ann@acme.test', linkedin='', project_id=None) -> None:
    cid = _campaign(c, uid, emails=(email,))
    if project_id:
        c.execute('UPDATE outreach_campaigns SET project_id = %s WHERE id = %s', (project_id, cid))
    c.execute(
        'UPDATE outreach_campaign_targets SET status = %s, linkedin_url = %s WHERE campaign_id = %s',
        (status, linkedin, cid),
    )


def _after(c, cid) -> str:
    return c.execute('SELECT status FROM outreach_campaign_targets WHERE campaign_id = %s', (cid,)).fetchone()['status']


def _resume(c) -> int:
    return each_org(c, holds.resume_paused, NOW)


def test_a_pause_ends_into_the_sequence(db):
    cid = _paused(db, _user(db))
    assert _resume(db) == 1 and _after(db, cid) == 'active'


def test_a_reply_in_another_campaign_ends_the_pause_as_a_reply(db):
    uid = _user(db)
    cid = _paused(db, uid)
    _elsewhere(db, uid, 'replied')
    assert _resume(db) == 0 and _after(db, cid) == 'replied'


def test_a_reply_from_an_alias_or_the_same_linkedin_profile_counts(db):
    uid = _user(db)
    person(db, project(db, uid), *ANN)
    by_alias = _paused(db, uid)
    _elsewhere(db, uid, 'replied', email='a.smith@acme.test')
    by_profile = _paused(db, uid, email='bea@acme.test', linkedin='https://www.linkedin.com/in/bea/')
    _elsewhere(db, uid, 'replied', email='bea.private@mail.test', linkedin='linkedin.com/in/bea')
    assert _resume(db) == 0
    assert (_after(db, by_alias), _after(db, by_profile)) == ('replied', 'replied')


def test_an_exclusion_in_another_campaign_ends_the_pause_excluded(db):
    uid = _user(db)
    cid = _paused(db, uid)
    _elsewhere(db, uid, 'excluded')
    assert _resume(db) == 0 and _after(db, cid) == 'excluded'


def test_a_stop_keeps_the_person_paused(db):
    uid = _user(db)
    cid = _paused(db, uid)
    stop(db, project(db, uid), 'ann@acme.test', 'failed')
    assert _resume(db) == 0 and _after(db, cid) == 'paused'


def test_another_client_never_decides_for_this_one(db):
    uid = _user(db)
    cid = _paused(db, uid)
    other = second_project(db, uid)
    _elsewhere(db, uid, 'replied', project_id=other)
    stop(db, other, 'ann@acme.test')
    assert _resume(db) == 1 and _after(db, cid) == 'active'


def _open_send(c, cid) -> str:
    return c.execute(
        """INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status, recipient,
               idempotency_key)
           SELECT t.campaign_id, t.owner_id, t.id, 1, 'email', 'awaiting_approval', t.email, 'k-' || t.id
             FROM outreach_campaign_targets t WHERE t.campaign_id = %s RETURNING target_id""",
        (cid,),
    ).fetchone()['target_id']


def _send(c, cid) -> tuple[str, str | None]:
    row = c.execute('SELECT status, error_code FROM outreach_campaign_sends WHERE campaign_id = %s', (cid,)).fetchone()
    return row['status'], row['error_code']


def _stops(webhook_events) -> list[tuple[str, str]]:
    return [(p['data']['target_id'], p['data']['reason']) for _, p in webhook_events if p['type'] == 'sequence.stopped']


def test_a_pause_that_ends_as_a_reply_skips_the_open_send_and_announces_the_stop(db, webhook_events):
    uid = _user(db)
    cid = _paused(db, uid)
    target = _open_send(db, cid)
    _elsewhere(db, uid, 'replied')
    _resume(db)
    assert _send(db, cid) == ('skipped', 'REPLIED')
    assert _stops(webhook_events) == [(str(target), 'replied')]


def test_a_pause_that_ends_excluded_skips_the_open_send_and_announces_the_stop(db, webhook_events):
    uid = _user(db)
    cid = _paused(db, uid)
    target = _open_send(db, cid)
    _elsewhere(db, uid, 'excluded')
    _resume(db)
    assert _send(db, cid) == ('skipped', 'EXCLUDED')
    assert _stops(webhook_events) == [(str(target), 'excluded')]


def test_a_resume_or_a_stop_leaves_the_open_send_and_announces_nothing(db, webhook_events):
    uid = _user(db)
    resumed = _paused(db, uid)
    _open_send(db, resumed)
    held = _paused(db, uid, email='bea@acme.test')
    _open_send(db, held)
    stop(db, project(db, uid), 'bea@acme.test', 'failed')
    assert _resume(db) == 1
    assert (_send(db, resumed), _send(db, held)) == (('awaiting_approval', None), ('awaiting_approval', None))
    assert _stops(webhook_events) == []
