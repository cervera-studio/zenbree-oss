"""The batch outcome of enrich_email / enrich_phone: counted by the row rule, one meaning for «failed» (#530 class)."""

import pytest
from pydantic import ValidationError

from app.agent.summary import summary_of
from app.agent.tools import registry
from app.agent.tools.handlers import enrich_email
from app.agent.tools.registry import ToolContext
from app.core.config import settings
from app.lists import lookup_outcome
from app.lists.lookup_outcome import Searched, Tally, kind_of, refunded, searched
from tests.web_fixtures import web_fixture

CTX = ToolContext(user_id='00000000-0000-0000-0000-000000000001', list_id='l1')
# Neither is an AI Ark id, so with Apollo off and only AI Ark keyed nobody can be asked about them.
NO_SOURCE_PEOPLE = [
    {'row_id': 'r1', 'first_name': 'Anna', 'last_name': 'Neri', 'company_domain': 'acme.it'},
    {'row_id': 'r2', 'first_name': 'Bruno', 'last_name': 'Gialli', 'company_domain': 'acme.it'},
]


@pytest.mark.web_fixture
async def test_a_batch_of_only_people_no_source_can_ask_reads_all_failed(http, keys, monkeypatch):
    """The live defect: such a person is «failed» on the row, but the batch read found 0, failed 0 (a green «0 email»)."""
    monkeypatch.setattr(settings, 'apollo_api_key', '')
    monkeypatch.setattr(settings, 'aiark_api_key', 'aiark-key')
    monkeypatch.setattr(enrich_email, 'load_rows', lambda list_id, row_ids: NO_SOURCE_PEOPLE)
    res = await enrich_email.handle({'row_ids': ['r1', 'r2']}, CTX)
    assert res.ok and res.data['found'] == 0
    assert res.data['failed'] == []  # the model's text keeps its filter: a retry finds the same sources
    tool = registry.get('enrich_email')
    assert summary_of(tool, res, {}) == res.outcome
    web_fixture('summary-email-no-source', res.outcome.model_dump(mode='json'))
    assert res.outcome.kind == 'all_failed' and res.outcome.tally.no_source == 2


def _email(row_id, **cells):
    return {'row_id': row_id, 'cells': {'email': None, **cells}}


def test_a_batch_is_counted_once_per_row_from_the_cells_the_call_wrote():
    updates = [
        _email('a', email='a@x.it', email_status='verified'),
        _email('b', email='b@x.it', email_status='invalid'),
        _email('c', email_status='not_found'),
        _email('d', email_status='failed'),
        _email('e', email_status='failed'),
    ]
    outcome = searched('email', updates, no_source={'e'}, unmatched=1)
    assert outcome.tally == Tally(found=1, invalid=1, not_found=1, failed=1, no_source=1)
    assert (outcome.kind, outcome.searched, outcome.unmatched) == ('partial', 5, 1)


def test_an_unsearchable_phone_row_is_no_input_not_a_miss():
    unsearchable = {'row_id': 'a', 'cells': {'phone': None}, 'if_empty': 'phone'}
    outcome = searched('phone', [unsearchable, {'row_id': 'b', 'cells': {'phone': None, 'phone_status': 'not_found'}}])
    assert outcome.tally == Tally(no_input=1, not_found=1)
    assert outcome.kind == 'none_found'


@pytest.mark.parametrize(
    ('tally', 'kind'),
    [
        (Tally(found=3), 'all_found'),
        (Tally(found=1, failed=2), 'partial'),
        (Tally(not_found=2), 'none_found'),
        (Tally(invalid=2), 'none_found'),
        (Tally(failed=1, no_source=2), 'all_failed'),
        (Tally(failed=1, not_found=1), 'none_found'),
    ],
)
def test_the_kind_comes_from_the_tally(tally, kind):
    assert kind_of(tally) == kind


def test_a_tally_that_lies_is_rejected():
    with pytest.raises(ValidationError, match='sum of the tally'):
        Searched(kind='all_found', contact='email', searched=3, tally=Tally(found=2))
    with pytest.raises(ValidationError, match='is not partial'):
        Searched(kind='all_found', contact='email', searched=3, tally=Tally(found=1, failed=2))


def test_refunded_sets_the_count_and_never_adds_to_it():
    outcome = searched('email', [_email('a', email='a@x.it', email_status='verified')])
    once = refunded(outcome, 2)
    assert once.refunded == 2 and outcome.refunded == 0
    assert refunded(once, 2).refunded == 2  # a retried settle changes nothing
    assert refunded(once, 1).refunded == 1
    gone = lookup_outcome.NoRowMatch(kind='no_row_match', contact='email')
    assert refunded(gone, 3) == gone


def test_a_result_that_writes_a_contact_lookup_without_an_outcome_is_refused():
    from app.agent.tools.registry import ToolResult

    with pytest.raises(ValueError, match='needs an outcome'):
        ToolResult(ok=True, row_updates=[{'row_id': 'a', 'cells': {'email_status': 'failed'}}])
    ToolResult(ok=True, row_updates=[{'row_id': 'a', 'cells': {'title': 'CTO'}}])  # not a contact write
