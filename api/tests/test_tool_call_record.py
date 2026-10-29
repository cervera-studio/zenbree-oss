"""``chat_messages.tool_calls`` is read through one record on both message routes, whatever generation wrote it.

Each fixture is a whole entry as the loop stored it at that time. Old rows are never migrated, so this is the one place
that reads them (``app/agent/tool_calls.py``); nothing may raise, or one old entry fails a whole session load.
"""

from uuid import UUID

import pytest
from psycopg.types.json import Jsonb

from app.admin.chats import AdminChatSession, chat_session
from app.agent.routes import ChatSessionDetail, get_session
from app.agent.summary import EntityCount
from app.agent.tool_calls import AdminToolCallRecord, ToolCallRecord
from app.core.system_reasons import SystemReason
from app.lists.lookup_outcome import Legacy, Searched, Tally
from tests.conftest import call_as, call_system, member, project
from tests.test_agent import db as db
from tests.test_agent import needs_db

M1 = {
    'id': 'm1',
    'name': 'search_companies',
    'input': {'q': 'x'},
    'result': '{"ok": 1}',
    'credits_used': 3,
    'verdict': 'success',
}
CAMPAIGN = {'name': 'Q4', 'steps': []}
DRAFT = {
    **M1,
    'summary': {'count': 3, 'noun': 'company'},
    'brief': {'who': ['cto']},
    'memory_saved': 'n',
    'campaign': CAMPAIGN,
}
VENDORS = {**DRAFT, 'vendors': ['apollo']}
EMAIL_530 = {**VENDORS, 'name': 'enrich_email', 'summary': {'count': 2, 'noun': 'email', 'searched': 4, 'failed': 1}}
PHONE_OLD = {**M1, 'name': 'enrich_phone', 'summary': {'count': 1, 'noun': 'phone', 'searched': 3, 'failed': 0}}
EMAIL_RETRY = {**M1, 'name': 'enrich_email', 'summary': {'count': 1, 'noun': 'email', 'searched': 3, 'failed': 1}}
ALL_FOUND = {**M1, 'name': 'enrich_email', 'summary': {'count': 3, 'noun': 'email', 'searched': 3, 'failed': 0}}
ALL_FAILED = {**M1, 'name': 'enrich_email', 'summary': {'count': 0, 'noun': 'email', 'searched': 2, 'failed': 2}}
EMPTY = {**M1, 'name': 'enrich_email', 'summary': {'count': 0, 'noun': 'email', 'searched': 0, 'failed': 0}}
EMAIL_666 = {**EMAIL_530, 'summary': {'count': 2, 'noun': 'email', 'searched': 4, 'failed': 1, 'refunded': 1}}
BARE_COUNT = {**M1, 'name': 'enrich_email', 'summary': {'count': 5, 'noun': 'email'}}
LIES = {**M1, 'name': 'enrich_email', 'summary': {'count': 9, 'noun': 'email', 'searched': 4, 'failed': 1}}
MALFORMED = {**M1, 'summary': 'oops'}
NO_NAME = {k: v for k, v in M1.items() if k != 'name'}
BAD_FIELDS = {**M1, 'input': 'not a dict', 'credits_used': 'many', 'verdict': 'maybe'}
STORED = [M1, DRAFT, VENDORS, EMAIL_530, EMAIL_666, BARE_COUNT, LIES, MALFORMED, NO_NAME, BAD_FIELDS, 'junk']
TALLIED = [PHONE_OLD, EMAIL_RETRY, ALL_FOUND, ALL_FAILED, EMPTY]


_tally = Tally


EXPECTED = [
    None,
    EntityCount(kind='count', noun='company', count=3),
    EntityCount(kind='count', noun='company', count=3),
    # The people left out of `failed` were not necessarily searched, so the remainder is not «not found».
    Legacy(kind='legacy', contact='email', found=2, failed=1),
    Legacy(kind='legacy', contact='email', found=2, failed=1, refunded=1),  # #666 stored the refund
    Legacy(kind='legacy', contact='email', found=5),
    Legacy(kind='legacy', contact='email', found=9, failed=1),  # counts that contradict each other
    None,  # a summary that cannot be read
]


def test_every_stored_generation_reads_into_the_record():
    calls = [
        ToolCallRecord.model_validate(e)
        for e in [M1, DRAFT, VENDORS, EMAIL_530, EMAIL_666, BARE_COUNT, LIES, MALFORMED]
    ]
    assert [c.summary for c in calls] == EXPECTED
    assert calls[0].model_dump(exclude={'summary'}) == {
        **M1,
        'brief': None,
        'memory_saved': None,
        'campaign': None,
        'error_code': None,
    }
    assert (calls[1].brief, calls[1].memory_saved, calls[1].campaign) == ({'who': ['cto']}, 'n', CAMPAIGN)
    assert AdminToolCallRecord.model_validate(VENDORS).vendors == ['apollo']
    assert not hasattr(ToolCallRecord.model_validate(VENDORS), 'vendors')  # the user route never sends them


def test_an_old_summary_shows_only_what_the_stored_counts_prove():
    got = [ToolCallRecord.model_validate(e).summary for e in TALLIED]
    assert got == [
        Legacy(kind='legacy', contact='phone', found=1),  # 2 of the 3 may never have been searched
        Legacy(kind='legacy', contact='email', found=1, failed=1),
        Searched(kind='all_found', contact='email', searched=3, tally=_tally(found=3)),
        Searched(kind='all_failed', contact='email', searched=2, tally=_tally(failed=2)),
        Searched(kind='none_found', contact='email', searched=0, tally=_tally()),
    ]


def test_an_old_row_keeps_the_refund_it_stored():
    # #666 stored `refunded`: 2 found of 4 searched is not determined, so it is `legacy`, and the refund stays.
    legacy = ToolCallRecord.model_validate(EMAIL_666).summary
    assert isinstance(legacy, Legacy)
    assert (legacy.found, legacy.failed, legacy.refunded) == (2, 1, 1)
    assert legacy.model_dump()['refunded'] == 1
    determined = {**EMAIL_666, 'summary': {'count': 3, 'noun': 'email', 'searched': 3, 'failed': 0, 'refunded': 2}}
    kept = ToolCallRecord.model_validate(determined).summary
    assert isinstance(kept, Searched) and kept.refunded == 2
    failed = {**EMAIL_666, 'summary': {'count': 0, 'noun': 'email', 'searched': 2, 'failed': 2, 'refunded': 1}}
    kept = ToolCallRecord.model_validate(failed).summary
    assert isinstance(kept, Searched) and kept.refunded == 1


def test_a_field_that_fails_to_parse_becomes_its_default_and_the_rest_is_kept():
    call = ToolCallRecord.model_validate(BAD_FIELDS)
    assert (call.name, call.input, call.credits_used, call.verdict) == ('search_companies', {}, 0, None)
    assert (call.id, call.result) == ('m1', '{"ok": 1}')
    # Put in, not left out: a route that sends only the fields set (exclude_unset) must still send the defaults.
    assert {'input', 'credits_used', 'verdict'} <= call.model_fields_set
    assert call.model_dump(exclude_unset=True)['input'] == {}


def test_an_unreadable_field_is_logged_once_per_entry():
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        ToolCallRecord.model_validate({**BAD_FIELDS, 'summary': 'oops'})
    assert sorted(e['event'] for e in logs) == ['chat.tool_call_field_unreadable', 'chat.tool_summary_unreadable']
    assert 'oops' not in str(logs)  # what a stored entry holds can name a person


def test_a_dropped_entry_logs_its_shape_and_never_its_text():
    from pydantic import TypeAdapter
    from structlog.testing import capture_logs

    from app.agent.tool_calls import ToolCalls

    entry = {'input': {'name': 'Ada Lovelace'}, 'result': 'ada@example.com'}
    with capture_logs() as logs:
        TypeAdapter(ToolCalls).validate_python([entry])
    assert [e['event'] for e in logs] == ['chat.tool_call_dropped']
    assert 'Ada' not in str(logs) and 'example.com' not in str(logs)
    assert logs[0]['keys'] == ['input', 'result'] and logs[0]['fields'] == ['name']


def _chat(db, calls):
    uid = db.execute(
        'INSERT INTO "user" (id, name, email, "emailVerified") VALUES (gen_random_uuid(), \'T\', gen_random_uuid() || \'@t.test\', true) RETURNING id'
    ).fetchone()['id']
    sid = db.execute(
        'INSERT INTO chat_sessions (organization_id, project_id, user_id) VALUES (default_organization(%s), %s, %s) RETURNING id',
        (uid, project(db, uid), uid),
    ).fetchone()['id']
    db.execute(
        "INSERT INTO chat_messages (session_id, user_id, role, content, tool_calls) VALUES (%s, %s, 'assistant', 'x', %s)",
        (sid, uid, Jsonb(calls)),
    )
    return uid, sid


@pytest.fixture
def lossless_response_models():
    """These rows are old on purpose: reading them changes the JSON, which this file asserts instead."""


@needs_db
def test_both_message_routes_read_every_generation_and_drop_what_is_no_call(db):
    uid, sid = _chat(db, STORED)
    mine = ChatSessionDetail.model_validate(call_as(member(db, uid), get_session, UUID(str(sid)), member(db, uid)))
    admin = AdminChatSession.model_validate(call_system(SystemReason.BACK_OFFICE, chat_session, UUID(str(sid))))
    for calls in (mine.messages[0].tool_calls, admin.messages[0].tool_calls):
        assert [c.id for c in calls] == ['m1'] * 8 + ['m1']  # the entry with no name and the junk are dropped
        assert [c.summary for c in calls][:8] == EXPECTED
    assert admin.messages[0].tool_calls[2].vendors == ['apollo']
    assert not hasattr(mine.messages[0].tool_calls[2], 'vendors')


def test_a_failed_calls_error_code_survives_a_reload():
    entry = {**M1, 'verdict': 'failed', 'error_code': 'no_credits'}
    assert ToolCallRecord.model_validate(entry).error_code == 'no_credits'
    assert ToolCallRecord.model_validate(M1).error_code is None
