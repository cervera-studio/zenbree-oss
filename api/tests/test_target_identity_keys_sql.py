import psycopg
import pytest

from app.core.migrate import MIGRATIONS
from app.outreach.reply_targets import LINKEDIN_KEY
from tests import test_dispatcher_sql
from tests.conftest import conninfo_of
from tests.test_dispatcher_sql import DB_URL, _campaign
from tests.test_home_notify_sql import _delivered
from tests.test_tools_sql import _user

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: target key SQL test NOT run')

db = test_dispatcher_sql.db
KEYS = '20261020112439_target_identity_keys.sql'


def test_the_migration_keys_existing_targets_as_the_due_query_matched_them_and_notifies_no_home(db):
    db.execute(
        'ALTER TABLE outreach_campaign_targets DROP COLUMN email_key, DROP COLUMN linkedin_key'
    )  # the fixture ran it on an empty schema: run it on these rows
    uid = _user(db)
    cid = _campaign(db, uid, emails=())
    people = {
        'mixed case': (' Anna@Client.TEST ', 'https://www.linkedin.com/in/Anna/?trk=x#top'),
        'no LinkedIn': ('bea@client.test', ''),
        'blank LinkedIn': ('carl@client.test', '   '),
        'no email': (None, 'linkedin.com/in/dee/'),
        'blank email': ('   ', 'www.linkedin.com/in/eve'),
        'empty email': ('', 'http://linkedin.com/in/fay'),
        'URL with no path': ('gil@client.test', 'https://'),
    }
    for name, (addr, linkedin) in people.items():
        db.execute(
            'INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, linkedin_url, first_name, status)'
            " VALUES (%s, %s, %s, %s, %s, 'active')",
            (cid, uid, addr, linkedin, name),
        )

    with psycopg.connect(conninfo_of(db), autocommit=True) as listen:
        listen.execute('LISTEN home')
        db.execute((MIGRATIONS / KEYS).read_text())
        assert _delivered(listen, db) == []  # a rewrite of every target refreshes no open Home

    keys = {
        r['first_name']: (r['email_key'], r['linkedin_key'], r['was_email'], r['was_linkedin'])
        for r in db.execute(
            f"""SELECT first_name, email_key, linkedin_key,
                       CASE WHEN coalesce(btrim(email), '') <> '' THEN lower(btrim(email)) END AS was_email,
                       CASE WHEN btrim(linkedin_url) <> '' THEN {LINKEDIN_KEY.format('linkedin_url')} END AS was_linkedin
                FROM outreach_campaign_targets"""
        )
    }
    assert {name: k[:2] for name, k in keys.items()} == {
        'mixed case': ('anna@client.test', 'linkedin.com/in/anna'),
        'no LinkedIn': ('bea@client.test', None),
        'blank LinkedIn': ('carl@client.test', None),
        'no email': (None, 'linkedin.com/in/dee'),
        'blank email': (None, 'linkedin.com/in/eve'),
        'empty email': (None, 'linkedin.com/in/fay'),
        'URL with no path': ('gil@client.test', ''),  # not blank: the due query compared it, and still does
    }
    assert all(k[:2] == k[2:] for k in keys.values())  # the keys the due query compared before the columns
