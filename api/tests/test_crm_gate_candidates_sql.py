"""The gate's queries match a verdict on the stored ``email_key`` (#1142). Needs TEST_DATABASE_URL."""

from datetime import timedelta

from app.core.scope import acting_for
from app.outreach import dispatcher
from tests import test_dispatcher_sql as dispatcher_tests
from tests.conftest import each_org
from tests.test_crm_gate_sql import _hubspot, _org
from tests.test_dispatcher_sql import NOW, _campaign, _sends
from tests.test_dispatcher_sql import db as db
from tests.test_dispatcher_sql import pytestmark as pytestmark
from tests.test_tools_sql import _user


def test_a_verdict_keeps_a_blocked_person_out_of_the_candidates_and_the_plan(db):
    uid = _user(db)
    _hubspot(db, uid)
    _campaign(db, uid, emails=(' Blocked@Client.test ', 'free@client.test'))
    db.execute(
        'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, linkedin_url, first_name, status)'
        " SELECT id, owner_id, '', 'https://www.linkedin.com/in/blank', 'Blank', 'active' FROM outreach_campaigns"
    )
    db.execute(
        'INSERT INTO crm_gate_checks (owner_id, email, reason, checked_at) VALUES (%s, %s, %s, %s)',
        (uid, 'blocked@client.test', 'HUBSPOT_OWNED', NOW),
    )
    with acting_for(_org(uid), 'campaign beat'):
        candidates = dispatcher.gate_candidates(NOW + timedelta(minutes=1))
    assert candidates == {uid: ['free@client.test']}  # the blank address is never read

    each_org(dispatcher_tests._DB[0], dispatcher.plan, NOW)
    blank = 'https://www.linkedin.com/in/blank'
    assert [s['recipient'] for s in _sends(db)] == [blank]
    db.execute(
        'INSERT INTO crm_gate_checks (owner_id, email, checked_at) VALUES (%s, %s, %s)',
        (uid, 'free@client.test', NOW),
    )
    each_org(dispatcher_tests._DB[0], dispatcher.plan, NOW)
    assert {s['recipient'] for s in _sends(db)} == {blank, 'free@client.test'}
