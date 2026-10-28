from app.automations import gate
from app.automations.models import AddToCampaign, Definition
from tests.test_dispatcher_sql import _campaign
from tests.test_holds_sql import db as db
from tests.test_holds_sql import lead, stop
from tests.test_tools_sql import _user


def test_a_stopped_or_instantly_held_person_is_refused_before_any_credit(db):
    uid = _user(db)
    campaign = _campaign(db, uid, emails=())
    pid = str(
        db.execute('SELECT project_id FROM outreach_campaigns WHERE id = %s', (campaign,)).fetchone()['project_id']
    )
    stop(db, pid, 'ann@acme.test')
    lead(db, pid, 'bea@acme.test')
    definition = Definition(
        list_id='00000000-0000-4000-8000-000000000001',
        steps=[AddToCampaign(type='add_to_campaign', campaign_id=campaign)],
    )
    refused = gate.rescreen(db, pid, uid, definition)
    people = ('ann@acme.test', 'bea@acme.test', 'cleo@acme.test')
    verdicts = [refused({'email': e, 'email_status': 'verified'}) for e in people]
    assert verdicts == ['stopped', 'held_by_instantly', None]
