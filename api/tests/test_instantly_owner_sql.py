"""Who may go to Instantly beside a native campaign (#931), against real SQL. Needs TEST_DATABASE_URL."""

import json

import pytest

from app.integrations import instantly_verdicts
from app.outreach.domain.campaign import validate_steps
from tests.conftest import project
from tests.test_dispatcher_sql import NOW, ONE_STEP
from tests.test_holds_sql import db as db
from tests.test_holds_sql import lead, stop
from tests.test_tools_sql import _user

LINKEDIN_ONLY = [{'step': 1, 'channel': 'linkedin_connect', 'body': 'Ciao {{firstName}}', 'send_after_days': 0}]
ANN = {'full_name': 'Ann Smith', 'email': 'ann@acme.test', 'linkedin_url': 'https://www.linkedin.com/in/ann'}


def _native(c, uid, steps, *, blocks: bool) -> None:
    cid = c.execute(
        """INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, steps,
               block_active_duplicates, activated_at)
           VALUES (default_organization(%s), %s, %s, 'N', 'active', %s, %s, %s) RETURNING id""",
        (uid, project(c, uid), uid, json.dumps(validate_steps(steps)), blocks, NOW),
    ).fetchone()['id']
    c.execute(
        'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, linkedin_url, status)'
        " VALUES (%s, %s, %s, %s, 'active')",
        (cid, uid, ANN['email'], ANN['linkedin_url']),
    )


def _reason(c, uid, enrollment_id=None) -> str | None:
    (verdict,) = instantly_verdicts.judge(
        c,
        project_id=project(c, uid),
        owner_id=uid,
        rows=[{'id': 'r1', 'cells': ANN, 'excluded': False}],
        enrollment_id=enrollment_id,
        check_crm=False,
    )
    return verdict.reason


@pytest.mark.parametrize(
    ('steps', 'blocks', 'reason'),
    [
        (LINKEDIN_ONLY, False, None),
        (LINKEDIN_ONLY, True, 'in_native_campaign'),
        (ONE_STEP, False, 'in_native_campaign'),
        (ONE_STEP, True, 'in_native_campaign'),
    ],
)
def test_the_native_campaign_that_holds_the_person_decides(db, steps, blocks, reason):
    uid = _user(db)
    _native(db, uid, steps, blocks=blocks)
    assert _reason(db, uid) == reason


def test_a_person_being_stopped_is_never_published_again(db):
    uid = _user(db)
    assert _reason(db, uid) is None
    stop(db, project(db, uid), ANN['email'], 'confirmed')
    assert _reason(db, uid) == 'stopped'


def test_a_retried_batch_is_not_held_by_its_own_leads(db):
    uid = _user(db)
    mine = lead(db, project(db, uid), ANN['email'], 'uncertain')
    batch = db.execute('SELECT enrollment_id FROM instantly_leads WHERE id = %s', (mine,)).fetchone()['enrollment_id']
    assert _reason(db, uid, str(batch)) is None
    assert _reason(db, uid) == 'already_in_instantly'
