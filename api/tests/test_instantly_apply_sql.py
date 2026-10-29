from datetime import timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from app.core import db as core_db
from app.core import scope as scopes
from app.core.errors import err
from app.core.migrate import MIGRATIONS
from app.core.scope import acting_for
from app.integrations.instantly_sync import effects, mapping, outcomes
from app.outreach import away, coordination, rejects, stop_outbox
from tests.conftest import migrated_database, project, scoped_conn
from tests.test_freeze_sql import _InAThread, _wait_for_a_lock
from tests.test_holds_sql import lead, person
from tests.test_instantly_events_sql import CAMPAIGN, DB_URL, NOW, WS, db, record, seed
from tests.test_instantly_sync_sql import Fake, beat, post, subscribe
from tests.test_replies_sql import _campaign
from tests.test_tools_sql import _user

pytestmark = pytest.mark.skipif(not DB_URL, reason='TEST_DATABASE_URL not set: Instantly effects SQL test NOT run')
fixtures = (db, post, beat)

ANA, BEA, CLEO = 'ana@acme.test', 'bea@acme.test', 'cleo@acme.test'
CAMPAIGN_2 = 'instantly-campaign-2'


class World:
    def __init__(self, c):
        self.c = c
        self.b = seed(c, (ANA, BEA, CLEO))
        p, self.org = self.b.project_id, self.b.organization_id
        a = str(c.execute('SELECT user_id FROM projects WHERE id = %s', (p,)).fetchone()['user_id'])
        colleague = _user(c)
        c.execute(
            'INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES (%s, %s, %s, now())',
            (self.org, colleague, 'member'),
        )
        q = str(
            c.execute(
                "INSERT INTO projects (organization_id, user_id, name) VALUES (%s, %s, 'Q') RETURNING id", (self.org, a)
            ).fetchone()['id']
        )
        self.owner = a
        person(c, p, ('email', ANA), ('email', 'a.smith@acme.test'))
        z = _user(c)
        self.zp = project(c, z)
        self.t = {
            'p': _campaign(c, a, email=ANA, client=p)['target'],
            'p_colleague': _campaign(c, colleague, email=ANA, client=p)['target'],
            'p_alias': _campaign(c, colleague, email='a.smith@acme.test', client=p)['target'],
            'p_bea': _campaign(c, a, email=BEA, client=p)['target'],
            'q': _campaign(c, a, email=ANA, client=q)['target'],
            'z': _campaign(c, z, email=ANA)['target'],
        }
        self.q = q
        held = c.execute('SELECT id::text FROM instantly_leads WHERE project_id = %s AND email = %s', (p, ANA))
        self.leads = {'first': held.fetchone()['id'], 'z': lead(c, self.zp, ANA)}
        self.leads['second'] = str(
            c.execute(
                'INSERT INTO instantly_leads (organization_id, project_id, enrollment_id, workspace_id, campaign_id,'
                ' email, outcome) SELECT organization_id, project_id, enrollment_id, workspace_id, %s, email, outcome'
                ' FROM instantly_leads WHERE id = %s RETURNING id',
                (CAMPAIGN_2, self.leads['first']),
            ).fetchone()['id']
        )

    def apply(self, *items) -> effects.Effects:
        with acting_for(self.org, 'test'), scoped_conn(self.c)() as conn:
            return effects.record_and_apply(conn, self.b, list(items))

    def retry(self) -> effects.Effects:
        with acting_for(self.org, 'test'), scoped_conn(self.c)() as conn:
            return effects.retry(conn, self.b)

    def only_this_connection_is_due(self) -> None:
        """The beat runs one organization at a time on the test's one connection."""
        self.c.execute("UPDATE instantly_connections SET status = 'disconnected' WHERE project_id = %s", (self.zp,))

    def refuse_emails(self) -> None:
        self.c.execute('UPDATE instantly_sync_state SET emails_refused_at = clock_timestamp()')

    def effect(self, kind='reply') -> dict:
        return self.c.execute(
            'SELECT applied_effect, effect_attempts, effect_error FROM instantly_events WHERE kind = %s', (kind,)
        ).fetchone()

    def statuses(self) -> dict[str, str]:
        by_id = {r['id']: r['status'] for r in self.c.execute('SELECT id, status FROM outreach_campaign_targets')}
        return {name: by_id[tid] for name, tid in self.t.items()}

    def stops(self) -> dict[str, tuple[str, str, bool]]:
        named = {v: k for k, v in self.leads.items()}
        rows = self.c.execute('SELECT lead_id::text, status, reason, keep_lead FROM outreach_stops').fetchall()
        return {named[r['lead_id']]: (r['status'], r['reason'], r['keep_lead']) for r in rows}

    def exclusions(self) -> set[tuple[str, str, str]]:
        rows = self.c.execute('SELECT project_id::text, value, origin FROM exclusions').fetchall()
        names = {self.b.project_id: 'p', self.q: 'q', self.zp: 'z'}
        return {(names[r['project_id']], r['value'], r['origin']) for r in rows}


@pytest.fixture
def world(db):
    return World(db)


def hook(event_type, *, email=ANA, campaign=CAMPAIGN, at=NOW, **extra):
    payload = {
        'event_type': event_type,
        'timestamp': at.isoformat(),
        'workspace': WS,
        'campaign_id': campaign,
        'lead_email': email,
        'step': 1,
        **extra,
    }
    return mapping.from_webhook(payload, now=NOW)


def api_reply(
    ref, *, auto, subject='Re: Your proposal', text='I am out of the office until Monday.', at=NOW, lead=ANA, **extra
):
    item = {
        'id': ref,
        'ue_type': 2,
        'lead': lead,
        'campaign_id': extra.pop('campaign', CAMPAIGN),
        'timestamp_email': at.isoformat(),
        'is_auto_reply': auto,
        'subject': subject,
        'body': {'text': text},
    }
    return mapping.from_email(item, now=NOW)


def bounced():
    return mapping.from_lead({'email': ANA, 'status': -1}, CAMPAIGN, now=NOW)


IN_P = {'p', 'p_colleague', 'p_alias'}


def _expect(stopped: set[str], status: str) -> dict[str, str]:
    return {
        name: status if name in stopped else 'active' for name in ('p', 'p_colleague', 'p_alias', 'p_bea', 'q', 'z')
    }


def test_a_human_reply_stops_the_person_in_the_project_and_queues_the_other_leads(world):
    world.apply(api_reply('em-1', auto=0, text='Sounds good, call me'))
    assert world.statuses() == _expect(IN_P, 'replied')
    assert world.stops() == {
        'first': ('pending', 'human_reply', True),
        'second': ('pending', 'human_reply', False),
    }
    assert world.exclusions() == set()


def test_instantly_unsubscribe_excludes_in_every_project_of_the_organization_that_wrote(world):
    world.apply(hook('lead_unsubscribed'))
    assert world.statuses() == _expect(IN_P | {'q'}, 'excluded')
    assert world.exclusions() == {('p', ANA, 'unsubscribed'), ('q', ANA, 'unsubscribed')}
    assert {k: v[1] for k, v in world.stops().items()} == {'first': 'opt_out', 'second': 'opt_out'}


def test_a_hard_bounce_excludes_as_bounced_and_a_webhook_bounce_alone_does_nothing(world):
    world.apply(hook('email_bounced'))
    assert world.statuses() == _expect(set(), 'active') and not world.stops() and not world.exclusions()
    world.apply(*bounced())
    assert world.statuses() == _expect(IN_P | {'q'}, 'excluded')
    assert world.exclusions() == {('p', ANA, 'bounced'), ('q', ANA, 'bounced')}
    assert {k: v[1] for k, v in world.stops().items()} == {'first': 'hard_bounce', 'second': 'hard_bounce'}


def test_a_person_only_instantly_wrote_to_is_excluded_in_the_connections_project(world):
    world.apply(hook('lead_unsubscribed', email=CLEO))
    assert world.exclusions() == {('p', CLEO, 'unsubscribed')}


def test_a_replay_changes_nothing(world, webhook_events):
    reply = api_reply('em-1', auto=0, text='Sounds good')
    world.apply(reply)
    before = (world.statuses(), world.stops(), len(webhook_events))
    again = world.apply(reply, reply)
    assert again.recorded.duplicate == 2 and not again.stopped
    assert (world.statuses(), world.stops(), len(webhook_events)) == before


def test_a_later_unsubscribe_after_a_reply_still_excludes(world):
    world.apply(api_reply('em-1', auto=0, text='Sounds good', at=NOW - timedelta(hours=1)))
    world.apply(hook('lead_unsubscribed'))
    assert world.statuses() == {**_expect(IN_P, 'replied'), 'q': 'excluded'}
    assert world.exclusions() == {('p', ANA, 'unsubscribed'), ('q', ANA, 'unsubscribed')}


def test_an_autoresponder_pauses_from_its_own_time_and_never_stops(world):
    at = NOW - timedelta(hours=2)
    world.apply(hook('auto_reply_received', at=at, reply_subject='Out of office', reply_text='Back on Monday'))
    assert world.statuses() == _expect(IN_P, 'paused') and not world.stops()
    until = {
        r['paused_until']
        for r in world.c.execute("SELECT paused_until FROM outreach_campaign_targets WHERE status = 'paused'")
    }
    assert until == {away.unknown_return_pause_end(at)}
    world.apply(api_reply('em-1', auto=0, text='Actually, call me', at=at + timedelta(hours=1)))
    assert world.statuses() == _expect(IN_P, 'replied')
    world.apply(hook('auto_reply_received', at=at + timedelta(hours=2), reply_subject='Out of office'))
    assert world.statuses() == _expect(IN_P, 'replied')


def test_the_lead_that_replies_later_becomes_one_to_keep(world):
    world.apply(api_reply('em-1', auto=0, text='Sounds good', at=NOW - timedelta(hours=1)))
    assert world.stops()['second'] == ('pending', 'human_reply', False)
    world.apply(api_reply('em-2', auto=0, text='Me too', campaign=CAMPAIGN_2))
    assert world.stops()['second'] == ('pending', 'human_reply', True)


def test_one_person_whose_effect_fails_never_blocks_the_others(world, monkeypatch):
    real = coordination.apply

    def poisoned(conn, signal):
        done = real(conn, signal)
        if signal.person.email == BEA:
            raise RuntimeError('poison')
        return done

    monkeypatch.setattr(effects.coordination, 'apply', poisoned)
    world.refuse_emails()
    done = world.apply(hook('reply_received', reply_text='Yes'), hook('reply_received', email=BEA, reply_text='Yes'))
    assert len(done.recorded.changed) == 2
    assert world.statuses()['p'] == 'replied' and world.statuses()['p_bea'] == 'active'
    attempts = world.c.execute("SELECT email, effect_attempts FROM instantly_events WHERE kind = 'reply'").fetchall()
    assert {r['email']: r['effect_attempts'] for r in attempts} == {ANA: 0, BEA: 1}


def test_a_webhook_reply_with_no_vote_stops_the_person_when_no_vote_can_come(world, post, webhook_events):
    sub = subscribe(world.c, world.b, status='live')
    res = post(sub, {'event_type': 'reply_received', 'reply_subject': 'Re: Ciao', 'reply_text': 'Sounds good'})
    assert res.json() == {'status': 'recorded'}
    assert world.statuses() == _expect(set(), 'active')  # the key may read emails: the API brings Instantly's vote
    world.refuse_emails()
    res = post(sub, {'event_type': 'reply_received', 'lead_email': BEA, 'reply_text': 'Sounds good'})
    assert res.json() == {'status': 'recorded'}
    assert world.statuses()['p_bea'] == 'replied'
    world.retry()
    assert world.statuses() == {**_expect(IN_P, 'replied'), 'p_bea': 'replied'}
    stopped = [p['data'] for _, p in webhook_events if p['type'] == 'sequence.stopped']
    assert {d['reason'] for d in stopped} == {'replied'} and len(stopped) == 1


def test_a_lock_that_fails_the_effect_fails_the_delivery_so_instantly_retries_it(world, post, monkeypatch):
    def locked(conn, signal):
        raise psycopg.errors.LockNotAvailable('project locked')

    monkeypatch.setattr(effects.coordination, 'apply', locked)
    sub = subscribe(world.c, world.b, status='live')
    assert post(sub, {'event_type': 'lead_unsubscribed'}).status_code == 503
    assert world.c.execute('SELECT count(*) AS n FROM instantly_events').fetchone()['n'] == 0


def test_a_webhook_reply_with_no_vote_waits_for_the_api_and_pauses_on_its_auto_vote(world):
    world.apply(hook('reply_received', reply_subject='Re: Your proposal', reply_text='I am away until Monday'))
    assert world.statuses() == _expect(set(), 'active') and not world.stops()
    world.apply(api_reply('em-1', auto=1, at=NOW + timedelta(seconds=40)))
    assert world.statuses() == _expect(IN_P, 'paused') and not world.stops()


def test_opt_out_text_beats_an_instantly_auto_vote(world):
    world.apply(api_reply('em-1', auto=1, text='Please remove me from your list'))
    assert world.statuses() == _expect(IN_P, 'replied')
    assert {k: v[1] for k, v in world.stops().items()} == {'first': 'opt_out', 'second': 'opt_out'}
    assert world.exclusions() == set()  # an opt-out read in the text stops the person and excludes nobody


def test_our_autoresponder_vote_then_instantly_human_vote_stops_the_paused_person(world):
    world.apply(hook('reply_received', reply_subject='Automatic reply: Ciao', reply_text='Back on Monday'))
    assert world.statuses() == _expect(IN_P, 'paused')
    world.apply(api_reply('em-1', auto=0, at=NOW + timedelta(seconds=40)))
    assert world.statuses() == _expect(IN_P, 'replied')


def _autoresponder_then_a_plain_reply(world) -> None:
    world.apply(hook('auto_reply_received', at=NOW - timedelta(minutes=7), reply_subject='Out of office'))
    world.apply(hook('reply_received', reply_subject='Re: Your proposal', reply_text='Please call me in November'))


def test_a_plain_reply_minutes_after_an_autoresponder_stops_when_no_vote_can_come(world):
    world.refuse_emails()
    _autoresponder_then_a_plain_reply(world)
    assert world.statuses() == _expect(IN_P, 'replied')
    assert {k: v[1] for k, v in world.stops().items()} == {'first': 'human_reply', 'second': 'human_reply'}


def test_a_plain_reply_minutes_after_an_autoresponder_waits_for_its_vote_when_one_can_come(world):
    _autoresponder_then_a_plain_reply(world)
    assert world.statuses() == _expect(IN_P, 'paused') and not world.stops()
    world.apply(api_reply('em-2', auto=0, text='Please call me in November'))
    assert world.statuses() == _expect(IN_P, 'replied')


def test_a_deleted_exclusion_stays_deleted_after_a_later_fact(world):
    world.apply(hook('lead_unsubscribed', at=NOW - timedelta(hours=1)))
    world.c.execute('DELETE FROM exclusions')
    world.apply(hook('email_sent', campaign=CAMPAIGN_2))
    assert world.exclusions() == set()


def test_a_person_enrolled_again_after_an_old_reply_stays_active_until_a_new_reply(world):
    world.apply(api_reply('em-1', auto=0, text='Not now, thanks', at=NOW - timedelta(hours=2)))
    again = _campaign(world.c, world.owner, email=ANA, client=world.b.project_id)['target']
    world.apply(hook('email_sent', campaign=CAMPAIGN_2, at=NOW - timedelta(hours=1)))
    assert world.c.execute('SELECT status FROM outreach_campaign_targets WHERE id = %s', (again,)).fetchone() == {
        'status': 'active'
    }
    world.apply(api_reply('em-2', auto=0, text='Call me now'))
    assert world.c.execute('SELECT status FROM outreach_campaign_targets WHERE id = %s', (again,)).fetchone() == {
        'status': 'replied'
    }


def test_an_effect_that_fails_is_kept_and_the_next_pass_applies_it(world, monkeypatch):
    real = coordination.apply

    def failing(conn, signal):
        raise RuntimeError('a transient failure')

    monkeypatch.setattr(effects.coordination, 'apply', failing)
    world.apply(hook('lead_unsubscribed'))
    assert world.statuses() == _expect(set(), 'active') and not world.exclusions()
    assert world.effect('unsubscribed') == {
        'applied_effect': None,
        'effect_attempts': 1,
        'effect_error': 'RuntimeError',
    }
    monkeypatch.setattr(effects.coordination, 'apply', real)
    world.retry()
    assert world.statuses() == _expect(IN_P | {'q'}, 'excluded')
    assert world.effect('unsubscribed') == {
        'applied_effect': 'unsubscribed',
        'effect_attempts': 0,
        'effect_error': None,
    }


def test_a_fact_whose_effect_always_fails_is_tried_a_bounded_number_of_times(world, monkeypatch):
    calls = []

    def poison(conn, signal):
        calls.append(signal.cause)
        raise RuntimeError('poison')

    monkeypatch.setattr(effects.coordination, 'apply', poison)
    world.apply(hook('lead_unsubscribed'))
    for _ in range(effects.MAX_ATTEMPTS + 2):
        world.retry()
    assert len(calls) == effects.MAX_ATTEMPTS
    assert world.effect('unsubscribed')['effect_attempts'] == effects.MAX_ATTEMPTS


def test_a_safer_vote_on_a_fact_whose_effect_gave_up_is_applied(world, monkeypatch):
    real = coordination.apply

    def poison(conn, signal):
        raise RuntimeError('poison')

    monkeypatch.setattr(effects.coordination, 'apply', poison)
    world.apply(hook('reply_received', reply_subject='Automatic reply: Ciao', reply_text='Back on Monday'))
    for _ in range(effects.MAX_ATTEMPTS):
        world.retry()
    assert world.effect()['effect_attempts'] == effects.MAX_ATTEMPTS
    monkeypatch.setattr(effects.coordination, 'apply', real)
    world.apply(api_reply('em-1', auto=0, at=NOW + timedelta(seconds=40)))
    assert world.statuses() == _expect(IN_P, 'replied')
    assert world.effect() == {'applied_effect': 'human_reply', 'effect_attempts': 0, 'effect_error': None}


def test_a_fact_recorded_before_the_migration_is_never_applied_on_a_later_fact(world):
    world.c.execute(
        'ALTER TABLE instantly_events DROP COLUMN our_class, DROP COLUMN hard_bounce, DROP COLUMN applied_effect,'
        ' DROP COLUMN effect_attempts, DROP COLUMN effect_error'
    )
    for kind, nature in (('reply', 'human'), ('unsubscribed', None)):
        world.c.execute(
            """INSERT INTO instantly_events (organization_id, project_id, connection_id, lead_id, workspace_id,
                   campaign_id, email, kind, reply_nature, fact_key, occurred_at, time_basis)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'event')""",
            (
                world.org,
                world.b.project_id,
                world.b.connection_id,
                world.leads['first'],
                WS,
                CAMPAIGN,
                ANA,
                kind,
                nature,
                f'old:{kind}',
                NOW - timedelta(days=1),
            ),
        )
    world.c.execute((MIGRATIONS / '20261017125629_instantly_votes.sql').read_text())  # the fixture ran it on no rows
    world.apply(hook('email_sent', campaign=CAMPAIGN_2))
    assert world.statuses() == _expect(set(), 'active') and not world.exclusions() and not world.stops()


def test_the_reconcile_pass_applies_a_failed_effect_even_when_instantly_fails(world, beat, monkeypatch):
    real = coordination.apply

    def failing(conn, signal):
        raise RuntimeError('a transient failure')

    monkeypatch.setattr(effects.coordination, 'apply', failing)
    world.apply(hook('lead_unsubscribed'))
    monkeypatch.setattr(effects.coordination, 'apply', real)
    fake = Fake()

    async def down(key, *, email_type, since, cursor):
        return err('INSTANTLY.UNAVAILABLE')

    fake.list_emails = down
    world.only_this_connection_is_due()
    beat(fake)
    assert world.statuses() == _expect(IN_P | {'q'}, 'excluded')


def test_the_reconcile_pass_that_finds_emails_refused_stops_a_reply_no_side_voted_on(world, beat):
    world.apply(hook('reply_received', reply_text='Sounds good'))
    assert world.statuses() == _expect(set(), 'active')
    fake = Fake()

    async def refused(key, *, email_type, since, cursor):
        return err('INSTANTLY.MISSING_SCOPE')

    fake.list_emails = refused
    world.only_this_connection_is_due()
    beat(fake)
    assert world.statuses() == _expect(IN_P, 'replied')


def test_a_pass_for_a_due_stop_alone_applies_a_failed_effect_and_keeps_the_read_schedule(world, beat, monkeypatch):
    real = coordination.apply

    def failing(conn, signal):
        raise RuntimeError('a transient failure')

    monkeypatch.setattr(effects.coordination, 'apply', failing)
    world.apply(hook('lead_unsubscribed'))
    monkeypatch.setattr(effects.coordination, 'apply', real)
    stop_outbox.request(world.c, project_id=world.b.project_id, emails=[BEA], reason='human_reply', cause='test')
    later = world.c.execute(
        "UPDATE instantly_sync_state SET next_run_at = now() + interval '1 hour', last_attempt_at = now()"
        ' WHERE connection_id = %s RETURNING next_run_at',
        (world.b.connection_id,),
    ).fetchone()['next_run_at']
    world.only_this_connection_is_due()
    fake = Fake()
    beat(fake)
    assert world.statuses() == _expect(IN_P | {'q'}, 'excluded')
    assert fake.calls == []
    state = world.c.execute(
        'SELECT next_run_at FROM instantly_sync_state WHERE connection_id = %s', (world.b.connection_id,)
    )
    assert state.fetchone()['next_run_at'] == later


def _retry_fails(monkeypatch) -> None:
    def down(conn, binding):
        raise psycopg.OperationalError('the database went away')

    monkeypatch.setattr(effects, 'retry', down)


def _sync_state(world) -> dict:
    return world.c.execute(
        'SELECT claim_token, next_run_at, last_error_code FROM instantly_sync_state WHERE connection_id = %s',
        (world.b.connection_id,),
    ).fetchone()


def test_a_retry_that_fails_never_keeps_the_pass_from_refusing_the_key_and_finishing(world, beat, monkeypatch):
    _retry_fails(monkeypatch)
    world.only_this_connection_is_due()
    beat(Fake(create=err('INSTANTLY.INVALID_KEY')))
    connection = world.c.execute('SELECT status FROM instantly_connections WHERE id = %s', (world.b.connection_id,))
    assert connection.fetchone()['status'] == 'error'
    state = _sync_state(world)
    assert state['claim_token'] is None and state['last_error_code'] == 'INSTANTLY.INVALID_KEY'


def test_a_retry_that_fails_never_keeps_a_pass_for_its_stops_from_releasing_the_lease(world, beat, monkeypatch):
    _retry_fails(monkeypatch)
    stop_outbox.request(world.c, project_id=world.b.project_id, emails=[BEA], reason='human_reply', cause='test')
    later = world.c.execute(
        "UPDATE instantly_sync_state SET next_run_at = now() + interval '1 hour', last_attempt_at = now()"
        ' WHERE connection_id = %s RETURNING next_run_at',
        (world.b.connection_id,),
    ).fetchone()['next_run_at']
    world.only_this_connection_is_due()
    beat(Fake())
    assert _sync_state(world) == {'claim_token': None, 'next_run_at': later, 'last_error_code': None}


def _in_its_own_transaction(conninfo, org_id, write):
    def run():
        with (
            acting_for(org_id, 'test'),
            psycopg.connect(conninfo, row_factory=dict_row) as conn,
            conn.transaction(),
            core_db.entered(conn, scopes.current()),
        ):
            return write(conn)

    return run


def test_a_page_of_two_people_waits_for_a_reject_of_both():
    with migrated_database() as (c, conninfo):
        b = seed(c, (ANA, BEA))
        record(c, b, *(api_reply(f'em-{n}', auto=0, text='Sounds good, call me', lead=n) for n in (ANA, BEA)))
        c.execute('UPDATE instantly_events SET effect_attempts = 1')
        page = _in_its_own_transaction(
            conninfo, b.organization_id, lambda conn: list(outcomes.for_emails(conn, b.project_id, [ANA, BEA]))
        )()
        owner = str(c.execute('SELECT user_id FROM projects WHERE id = %s', (b.project_id,)).fetchone()['user_id'])
        campaign = c.execute(
            'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, status)'
            " VALUES (%s, %s, %s, 'C', 'active') RETURNING id::text, project_id::text, owner_id::text",
            (b.organization_id, b.project_id, owner),
        ).fetchone()
        # The person the page applies first has the larger id, so the page locks against id order.
        larger, smaller = 'ffffffff-ffff-4fff-bfff-ffffffffffff', '00000000-0000-4000-8000-000000000001'
        sends = []
        for target, email in zip((larger, smaller), page, strict=True):
            c.execute(
                'INSERT INTO outreach_campaign_targets (id, campaign_id, owner_id, email, first_name, status, next_step)'
                " VALUES (%s, %s, %s, %s, 'A', 'active', 1)",
                (target, campaign['id'], owner, email),
            )
            sends += c.execute(
                'INSERT INTO outreach_campaign_sends (campaign_id, owner_id, target_id, step, channel, status,'
                " recipient, subject, idempotency_key) VALUES (%s, %s, %s, 1, 'email', 'awaiting_approval', %s,"
                " 'Ciao', %s) RETURNING id",
                (campaign['id'], owner, target, email, f'{target}:1'),
            ).fetchall()
        send_ids = [r['id'] for r in sends]
        reject = _InAThread(
            c,
            _in_its_own_transaction(conninfo, b.organization_id, lambda conn: rejects.reject(conn, campaign, send_ids)),
        )
        reconcile = _InAThread(
            c, _in_its_own_transaction(conninfo, b.organization_id, lambda conn: len(effects.retry(conn, b).stopped))
        )

        with psycopg.connect(conninfo) as blocker, blocker.transaction():
            locked = blocker.execute(
                'SELECT 1 FROM outreach_campaign_targets WHERE id = %s FOR NO KEY UPDATE', (smaller,)
            ).fetchall()
            assert locked == [(1,)]
            reject.start_and_wait_on_a_lock()
            reconcile.thread.start()
            _wait_for_a_lock(c, waiters=2)
        for side in (reject, reconcile):
            side.thread.join(10)

        assert (reject.result, reconcile.result) == (2, 2)
        assert [(r['status'], r['error_code']) for r in c.execute('SELECT * FROM outreach_campaign_sends')] == [
            ('skipped', 'REJECTED')
        ] * 2
        assert {r['status'] for r in c.execute('SELECT status FROM outreach_campaign_targets')} == {'replied'}
