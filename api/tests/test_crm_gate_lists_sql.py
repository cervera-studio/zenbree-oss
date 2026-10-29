"""The list row says why the HubSpot read gate holds a person (#120). Needs TEST_DATABASE_URL."""

from datetime import UTC, datetime, timedelta

from tests.test_agent import _chat, needs_db
from tests.test_agent import client as client  # list routes on the app's own pool
from tests.test_agent import db as db
from tests.test_crm_gate_sql import _hubspot


@needs_db
def test_list_row_says_why_the_gate_holds_a_person(client, db):
    """Done when (#120): the list row says why; nothing once the hold lapses or HubSpot is disconnected."""
    from app.lists.rows import add_rows, create_list

    tc, uid = client
    lid = create_list(db, user_id=uid, session_id=_chat(db, uid), name='People', entity='person')
    add_rows(
        db,
        lid,
        'person',
        [
            {'full_name': 'Anna', 'email': ' Anna@Client.test', 'linkedin_url': 'linkedin.com/in/anna'},
            {'full_name': 'Bea', 'email': 'bea@client.test', 'linkedin_url': 'linkedin.com/in/bea'},
        ],
    )
    _hubspot(db, uid)
    until = datetime.now(UTC) + timedelta(days=25)
    db.execute(
        'INSERT INTO crm_gate_checks (owner_id, email, reason, hold_until) VALUES'
        " (%s, 'anna@client.test', 'HUBSPOT_COOLDOWN', %s), (%s, 'bea@client.test', NULL, NULL)",
        (uid, until, uid),
    )

    def holds():
        rows = tc.get(f'/v1/lists/{lid}/rows').json()['rows']
        return {r['cells']['full_name']: r['crm_hold'] for r in rows}

    got = holds()
    assert got['Bea'] is None and got['Anna']['reason'] == 'HUBSPOT_COOLDOWN'
    assert datetime.fromisoformat(got['Anna']['until']) == until

    db.execute("UPDATE crm_gate_checks SET hold_until = now() - interval '1 minute'")
    assert holds() == {'Anna': None, 'Bea': None}  # lapsed: sendable again
    db.execute('UPDATE crm_gate_checks SET hold_until = %s', (until,))
    db.execute("UPDATE user_oauth_connections SET status = 'disconnected' WHERE provider = 'hubspot'")
    assert holds() == {'Anna': None, 'Bea': None}  # the dispatcher ignores it too
