from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.people.person_name import first_and_last
from app.scheduling.models import SearchInput
from app.scheduling.runner import identity


def test_bounded_saved_search_inputs():
    base = {'name': 'Weekly', 'tool_name': 'search_people', 'list_id': str(uuid4())}
    for patch in (
        {'budget_credits_per_run': 0},
        {'budget_credits_per_run': True},
        {'budget_credits_per_run': 2},
        {'tool_name': 'enrich_email'},
        {'tool_input': {'source': 'unknown'}},
        {'tool_input': {'page': 0}},
        {'tool_input': {'page': True}},
        {'tool_input': {'keywords': ['x'] * 21}},
        {'tool_input': {'row_ids': ['foreign']}},
        {'tool_input': {'employee_size': [5, 1]}},
        {'next_run_at': '2026-10-01T09:00:00'},
        {'tool_name': 'search_companies', 'campaign_id': str(uuid4())},
    ):
        with pytest.raises(ValidationError):
            SearchInput.model_validate({**base, **patch})
    data = SearchInput.model_validate({**base, 'next_run_at': '2026-10-01T09:00:00+02:00'})
    assert data.next_run_at == datetime(2026, 10, 1, 7, tzinfo=UTC)
    for patch in ({'source': 'apollo'}, {'page': 101}, {'limit': 61}, {'local_query': 'x' * 201}):
        with pytest.raises(ValidationError):
            SearchInput.model_validate({**base, 'tool_name': 'search_companies', 'tool_input': patch})
    assert identity(' ADA@Example.test ', 'https://www.LinkedIn.com/in/ada/?trk=x#y') == (
        'ada@example.test',
        'linkedin.com/in/ada',
    )


async def test_schedule_tool_uses_context_list(monkeypatch):
    from app.agent.tools.handlers import scheduler
    from app.agent.tools.registry import ToolContext

    user, lid = str(uuid4()), str(uuid4())
    saved = []

    def save(uid, body):
        saved.append((uid, body))
        return {'id': uuid4(), 'name': body.name, 'cadence': body.cadence}

    monkeypatch.setattr(scheduler.service, 'save', save)
    result = await scheduler.schedule({'name': 'Weekly', 'tool_name': 'search_people'}, ToolContext(user, list_id=lid))
    assert result.ok and str(saved[0][1].list_id) == lid and saved[0][0] == user
    assert 'list_id' not in scheduler.schedule_schema['required']
    assert 'list_id' in SearchInput.model_json_schema()['required']


@pytest.mark.parametrize(
    ('cells', 'expected'),
    [
        ({'full_name': 'Eva De Luca'}, ('Eva', 'De Luca')),  # #235: one name column
        ({'full_name': '  Eva  '}, ('Eva', '')),
        ({'full_name': 'Dott.ssa Eva De Luca'}, ('Eva', 'De Luca')),
        ({'full_name': 'dott. ing. Mario Rossi'}, ('Mario', 'Rossi')),
        ({'full_name': 'Dr Mario Rossi'}, ('Mario', 'Rossi')),
        ({'full_name': 'Sig.ra Anna Bianchi'}, ('Anna', 'Bianchi')),
        ({'full_name': 'Prof. Avv. Luca Neri'}, ('Luca', 'Neri')),
        ({'full_name': 'Ing Chen'}, ('Ing', 'Chen')),  # a given name without the dot stays
        ({'full_name': 'Dr.'}, ('Dr.', '')),  # a title with nothing after it is not stripped
        ({'full_name': 'Drago Rossi'}, ('Drago', 'Rossi')),
        ({'first_name': 'Eva', 'full_name': 'Eva De Luca'}, ('Eva', 'De Luca')),
        ({'last_name': 'De Luca', 'full_name': 'Eva De Luca'}, ('Eva', 'De Luca')),
        # Both parts stored (Apollo's obfuscated surname): full_name changes nothing.
        ({'first_name': 'Maria Pia', 'last_name': 'R***i', 'full_name': 'Maria Pia R***i'}, ('Maria Pia', 'R***i')),
        ({'first_name': 'Ada'}, ('Ada', '')),
        ({'first_name': None, 'last_name': None, 'full_name': None}, ('', '')),
    ],
)
def test_first_and_last_falls_back_to_full_name(cells, expected):
    assert first_and_last(cells) == expected
