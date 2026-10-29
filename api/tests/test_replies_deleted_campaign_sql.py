"""Mail to the address of a campaign whose client was deleted still reaches the person (#764 review, B1).

``DELETE /v1/projects/{id}`` cascades the client's campaigns, sent ones included. Their mail named the campaign in its
``Reply-To`` and ``List-Unsubscribe`` mailto, and an opt-out to it must still stop the person everywhere, as one to the
creator's old address does (#460). The deleted campaign leaves its routing key (outreach_campaign_reply_keys), and
the reply is matched as the old form would match it.

Needs TEST_DATABASE_URL.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.migrate import MIGRATIONS
from app.core.reply_address import build_campaign_reply_address, build_reply_address
from app.main import app
from tests.conftest import member, project, sign_in
from tests.test_replies_orgs_sql import _events, _filed_in, _reply_to
from tests.test_replies_orgs_sql import db as db
from tests.test_replies_orgs_sql import world as world
from tests.test_replies_sql import _post, _target

MIGRATION = '20261014050144_campaign_reply_keys.sql'
MAILS = {
    'mailto': {'subject': 'unsubscribe', 'text': ''},
    'free text': {'subject': 'Re: Ciao', 'text': 'Please remove me from your list'},
    'reply': {'subject': 'Re: Ciao', 'text': 'Volentieri, sentiamoci'},
}


def _delete_client(w) -> dict:
    """The creator runs campaign X in a second client of their own organization A, and campaigns in A's first client
    and in a client of organization B, all to Anna. They delete X's client through the route: X goes with it."""
    b_org, b_project = w.client()
    w.endpoint(w.own)
    w.endpoint(b_org)
    a_project = project(w.c, w.uid)
    x_client = w.c.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'X') RETURNING id::text",
        (w.own, w.uid),
    ).fetchone()['id']
    ca, cb, cx = w.campaign(age_days=3), w.campaign(b_project, age_days=1), w.campaign(x_client, age_days=0)
    addresses = {
        'campaign': _reply_to(cx),
        'old': build_reply_address(w.uid, domain='in.zenbree.test', secret='reply-secret'),
    }
    sign_in(app, member(w.c, w.uid))
    try:
        assert TestClient(app).delete(f'/v1/projects/{x_client}').status_code == 200
    finally:
        app.dependency_overrides.clear()
    assert not w.c.execute('SELECT 1 FROM outreach_campaigns WHERE id = %s', (cx['campaign'],)).fetchone()
    return {'a': ca, 'b': cb, 'a_project': a_project, 'b_org': b_org, 'b_project': b_project, **addresses}


@pytest.mark.parametrize('form', ['campaign', 'old'])
@pytest.mark.parametrize('mail', ['mailto', 'free text'])
def test_an_opt_out_to_a_deleted_clients_campaign_stops_the_person_everywhere(world, webhook_events, form, mail):
    """The ``old`` form is what ``main`` mints: the campaign form must do what it does."""
    w = world
    s = _delete_client(w)
    res = _post(TestClient(app), w.uid, to=s[form], **MAILS[mail])
    assert res.json()['status'] == 'accepted'
    assert res.json()['kind'] == 'unsubscribe' and res.json()['targets'] == 2
    assert [_target(w.c, t['target'])['status'] for t in (s['a'], s['b'])] == ['replied', 'replied']
    assert w.excluded_in() == {s['a_project'], s['b_project']}
    assert sorted(_events(webhook_events, 'sequence.stopped')) == sorted([str(w.own), str(s['b_org'])])


@pytest.mark.parametrize('form', ['campaign', 'old'])
def test_a_reply_to_a_deleted_clients_campaign_is_filed_as_the_old_address_files_it(world, webhook_events, form):
    """No campaign to name: the person's newest mail decides, B's here, and only B is written."""
    w = world
    s = _delete_client(w)
    res = _post(TestClient(app), w.uid, to=s[form], **MAILS['reply'])
    assert res.json()['status'] == 'accepted' and res.json()['targets'] == 1
    assert _filed_in(w.c, res.json()['id']) == s['b_project']
    assert (
        w.c.execute('SELECT campaign_id FROM outreach_inbound_messages').fetchone()['campaign_id'] == s['b']['campaign']
    )
    assert [_target(w.c, t['target'])['status'] for t in (s['a'], s['b'])] == ['active', 'replied']
    assert _events(webhook_events, 'contact.replied') == [str(s['b_org'])]
    assert w.excluded_in() == set()


def _tombstones(c) -> list[dict]:
    return c.execute(
        'SELECT campaign_id, organization_id::text, reply_key::text FROM outreach_campaign_reply_keys'
    ).fetchall()


def test_a_deleted_campaign_keeps_the_key_its_old_address_carried(world):
    """Its creator's; their former id once they deleted their account (#333), which a project delete leaves."""
    w = world
    _, b_project = w.client()
    ca, cb = w.campaign(), w.campaign(b_project)
    w.c.execute(
        'UPDATE outreach_campaigns SET owner_id = NULL, former_owner_id = %s WHERE id = %s', (w.uid, cb['campaign'])
    )
    w.c.execute('DELETE FROM outreach_campaigns WHERE id = ANY(%s)', ([ca['campaign'], cb['campaign']],))
    assert sorted(_tombstones(w.c), key=lambda r: r['organization_id'] != w.own) == [
        {'campaign_id': ca['campaign'], 'organization_id': w.own, 'reply_key': w.uid},
        {
            'campaign_id': cb['campaign'],
            'organization_id': w.c.execute(
                'SELECT organization_id::text AS o FROM projects WHERE id = %s', (b_project,)
            ).fetchone()['o'],
            'reply_key': w.uid,
        },
    ]


def test_a_signed_campaign_address_that_was_never_minted_is_dropped(world):
    """An id that never existed is a forgery with the secret: no key, so nothing is stopped."""
    w = world
    ca = w.campaign()
    to = build_campaign_reply_address(str(uuid.uuid4()), domain='in.zenbree.test', secret='reply-secret')
    assert _post(TestClient(app), w.uid, to=to, subject='unsubscribe', text='').json() == {'status': 'ignored'}
    assert _tombstones(w.c) == []
    assert _target(w.c, ca['target'])['status'] == 'active'
    assert w.excluded_in() == set()


def test_an_organization_delete_takes_its_campaigns_keys_with_it(world):
    """The trigger writes no key into the organization being deleted: that insert would fail the delete."""
    w = world
    b_org, b_project = w.client()
    w.campaign(b_project)
    w.c.execute('DELETE FROM organization WHERE id = %s', (b_org,))
    assert _tombstones(w.c) == []


def test_the_migration_runs_on_existing_campaigns_and_backfills_nothing(world):
    """Before it, no sent mail named a campaign: a campaign that exists keeps no key until it is deleted."""
    w = world
    w.c.execute(
        'DROP TRIGGER keep_reply_key ON outreach_campaigns; DROP FUNCTION keep_campaign_reply_key();'
        ' DROP TABLE outreach_campaign_reply_keys'
    )
    camp = w.campaign()
    w.c.execute((MIGRATIONS / MIGRATION).read_text())  # the fixture ran it on an empty schema: run it on this data
    assert _tombstones(w.c) == []
    w.c.execute('DELETE FROM outreach_campaigns WHERE id = %s', (camp['campaign'],))
    assert [r['campaign_id'] for r in _tombstones(w.c)] == [camp['campaign']]
