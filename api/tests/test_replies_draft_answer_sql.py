"""An inbox answer can mint a draft's campaign address; deleting the draft must not lose the opt-out (#764 review, B2).

The ranking can file a reply under a draft campaign, and ``POST /v1/inbox/{id}/reply`` then puts that draft's
campaign address in ``Reply-To`` and in the ``List-Unsubscribe`` mailto. ``DELETE /v1/campaigns/{id}`` deletes a draft,
and an opt-out to the address must still stop the person, as the owner-form address of ``main`` does.

Needs TEST_DATABASE_URL.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.reply_address import parse_reply_address
from app.main import app
from tests.conftest import member, sign_in
from tests.test_inbox_reply_sql import db as db
from tests.test_inbox_reply_sql import owner as owner
from tests.test_inbox_reply_sql import provider as provider
from tests.test_replies_sql import _campaign, _post, _target

MAILS = {
    'mailto': {'subject': 'unsubscribe', 'text': ''},
    'free text': {'subject': 'Re: Ciao', 'text': 'Please remove me from your list'},
}


@pytest.mark.parametrize('mail', ['mailto', 'free text'])
def test_an_opt_out_to_a_deleted_drafts_answer_address_stops_the_person(owner, db, mail):
    uid = owner['uid']
    project_id = db.execute(
        'SELECT project_id FROM outreach_campaigns WHERE id = %s', (owner['camp']['campaign'],)
    ).fetchone()['project_id']
    draft = _campaign(db, uid, client=str(project_id))
    db.execute("UPDATE outreach_campaigns SET status = 'draft' WHERE id = %s", (draft['campaign'],))
    db.execute(
        "UPDATE outreach_campaign_targets SET status = 'draft' WHERE id = %s",
        (draft['target'],),
    )
    db.execute('DELETE FROM outreach_campaign_sends WHERE campaign_id = %s', (draft['campaign'],))
    # The filing the ranking reaches after a client delete: the message sits under the draft.
    db.execute(
        'UPDATE outreach_inbound_messages SET campaign_id = %s, target_id = %s WHERE id = %s',
        (draft['campaign'], draft['target'], owner['id']),
    )

    res = owner['client'].post(f'/v1/inbox/{owner["id"]}/reply', json={'body': 'Giovedì alle 11 va bene.'})
    assert res.status_code == 200 and res.json()['status'] == 'sent'
    assert len(owner['sent']) == 1  # the stubbed mailer: the one answer, nothing else leaves
    answer, _ = owner['sent'][0]
    named = parse_reply_address(answer['Reply-To'], secret='reply-secret')
    assert named.campaign and named.id == str(draft['campaign'])
    assert f'<mailto:{answer["Reply-To"]}?subject=unsubscribe>' in answer['List-Unsubscribe']

    sign_in(app, member(db, uid))
    assert TestClient(app).delete(f'/v1/campaigns/{draft["campaign"]}').status_code == 204
    other = _campaign(db, uid, client=str(project_id))
    out = _post(TestClient(app), uid, to=answer['Reply-To'], email_id='u1', **MAILS[mail])
    assert out.json()['status'] == 'accepted' and out.json()['kind'] == 'unsubscribe'
    assert _target(db, other['target'])['status'] == 'replied'
    assert db.execute("SELECT count(*) AS n FROM exclusions WHERE value = 'anna@client.test'").fetchone()['n'] == 1
    assert len(owner['sent']) == 1
