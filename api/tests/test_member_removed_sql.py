"""#411: removing a member from an organization disconnects and unlinks the senders they connected there, so the
team stops sending as them; their senders in other organizations do not change.

Real SQL (the migrations and the trigger); the member is removed the way Better Auth's ``organization/remove-member``
does it in the end: ``DELETE FROM member``.
"""

import json
from datetime import timedelta

from app.linkedin import api as linkedin_api
from app.outreach import dispatcher
from app.outreach.domain.campaign import validate_steps
from tests.conftest import call_as, each_org, each_org_async, member
from tests.test_agent import db as db
from tests.test_agent import needs_db
from tests.test_creator_deleted_sql import _mailbox, _org, _org_project, _team
from tests.test_dispatcher_sql import LINKEDIN_STEPS, NOW, ONE_STEP
from tests.test_dispatcher_sql import provider as provider
from tests.test_dispatcher_sql import switches as switches
from tests.test_dispatcher_sql import unipile as unipile

pytestmark = needs_db


def _campaign(db, owner, org, pid, steps) -> None:
    cid = db.execute(
        """INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status, steps,
               activated_at, approve_before_sending)
           VALUES (%s, %s, %s, 'C', 'active', %s, %s, false) RETURNING id""",
        (org, pid, owner, json.dumps(validate_steps(steps)), NOW - timedelta(hours=1)),
    ).fetchone()['id']
    db.execute(
        'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, first_name, company_name, status,'
        " linkedin_url) VALUES (%s, %s, 'anna@client.test', 'Anna', 'Client', 'active',"
        " 'https://www.linkedin.com/in/anna-rossi/')",
        (cid, owner),
    )


def _senders(db, uid) -> dict:
    return {
        r['account_label']: r
        for r in db.execute(
            'SELECT u.account_label, u.organization_id::text AS org, u.status, u.status_detail,'
            ' u.refresh_token_encrypted, u.granted_scopes, u.provider_account_id, count(ps.project_id) AS links'
            ' FROM user_oauth_connections u LEFT JOIN project_senders ps ON ps.sender_id = u.id'
            ' WHERE u.user_id = %s GROUP BY u.id',
            (uid,),
        ).fetchall()
    }


def _account(db, user, pid):
    """The ``linkedin_search`` tool's lookup, which runs in a turn, so as the member."""
    return call_as(member(db, user), linkedin_api.connected_account_id, pid)


async def test_a_removed_members_senders_stop_sending_for_the_organization(db, provider, unipile, switches):
    ada, bo, org, pid = _team(db)
    _mailbox(db, bo, org, pid, 'bo@agency.test')
    _mailbox(db, bo, org, pid, 'acc-1', provider='unipile')
    _mailbox(db, bo, _org(db, bo), _org_project(db, bo), 'bo@own.test')
    _campaign(db, ada, org, pid, ONE_STEP)
    _campaign(db, ada, org, pid, LINKEDIN_STEPS)
    assert _account(db, ada, pid) == 'acc-1'

    db.execute('DELETE FROM member WHERE "userId" = %s AND "organizationId" = %s', (bo, org))

    senders = _senders(db, bo)
    for label in ('bo@agency.test', 'acc-1'):
        row = senders[label]
        assert (row['status'], row['status_detail'], row['refresh_token_encrypted'], row['links']) == (
            'disconnected',
            'owner_removed',
            '',
            0,
        )
        assert row['granted_scopes'] == []
    assert senders['acc-1']['provider_account_id'] is None  # freed: unique across organizations (#317)
    assert senders['bo@agency.test']['provider_account_id'] == 'bo@agency.test'
    own = senders['bo@own.test']
    assert (own['status'], own['status_detail'], own['links']) == ('connected', None, 1)
    assert own['refresh_token_encrypted'] != ''

    each_org(db, dispatcher.plan, NOW)
    assert (await each_org_async(db, dispatcher.send_due, NOW))['sent'] == 0
    assert (await each_org_async(db, dispatcher.send_linkedin_due, NOW))['linkedin_sent'] == 0
    assert provider.sent == [] and unipile.calls == []
    assert _account(db, ada, pid) is None
