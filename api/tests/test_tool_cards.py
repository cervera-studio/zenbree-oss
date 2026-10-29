import ast
import asyncio
import inspect

from app.agent.summary import EntityCount, summary_of
from app.agent.tools import registry
from app.agent.tools.handlers import ask_user as ask_user_module  # noqa: F401  (registers the handlers)
from app.agent.tools.handlers.ask_user import ask_user
from app.agent.tools.handlers.set_brief import set_brief
from app.agent.tools.registry import ToolContext, ToolResult
from app.core.locale import LOCALES
from app.lists.lookup_outcome import NoRowMatch, NothingToSearch, Searched, Tally, kind_of

CTX = ToolContext(user_id='u')


def test_ask_user_options_carry_descriptions_and_bare_strings_stay_valid():
    out = asyncio.run(
        ask_user(
            {
                'question': 'Email needed?',
                'options': [
                    {'label': 'Yes', 'description': ' Only verified. '},
                    'No',
                    {'label': ' '},
                    {'description': 'x'},
                ],
            },
            CTX,
        )
    )
    assert out.data['options'] == [
        {'label': 'Yes', 'description': 'Only verified.'},
        {'label': 'No', 'description': None},
    ]


def test_ask_user_drops_non_string_and_repeated_labels():
    out = asyncio.run(
        ask_user({'question': 'Q?', 'options': [{'label': {'a': 1}}, 7, 'Yes', {'label': ' yes '}, ['x']]}, CTX)
    )
    assert [o['label'] for o in out.data['options']] == ['Yes']


def test_set_brief_keeps_known_fields_and_drops_the_rest():
    out = asyncio.run(set_brief({'who': ['CEO', ' '], 'where': ['Milano'], 'email': 'maybe', 'secret': ['x']}, CTX))
    expected = {'who': ['CEO'], 'companies': [], 'where': ['Milano'], 'signal': [], 'email': None}
    assert out.ok and out.data == expected and out.brief == expected


def test_set_brief_drops_non_string_and_repeated_chips():
    out = asyncio.run(set_brief({'who': ['CEO', {'a': 1}, 3, 'ceo', ' CEO ', 'CTO']}, CTX))
    assert out.brief and out.brief['who'] == ['CEO', 'CTO']


def test_an_empty_brief_is_a_no_op_not_a_failed_step():
    out = asyncio.run(set_brief({'who': [' '], 'email': 'maybe'}, CTX))
    assert out.ok and out.brief is None


def _tool(name: str) -> registry.ToolDef:
    tool = registry.get(name)
    assert tool
    return tool


def _count(noun, count):
    return EntityCount(kind='count', noun=noun, count=count)


def test_summary_counts_what_the_list_gained():
    rows = ToolResult(ok=True, rows=[{}, {}, {}, {}], entity='person')
    gained = {'rows_added': 2, 'rows_updated': 1}
    assert summary_of(_tool('search_people'), rows, gained) == _count('person', 3)
    assert summary_of(_tool('search_people'), rows, {'rows_added': 0, 'rows_updated': 0}) == _count('person', 0)


def _outcome(contact, **tally):
    tally = Tally(**tally)
    return Searched(kind=kind_of(tally), contact=contact, searched=tally.total, tally=tally)


def test_the_summary_of_a_contact_call_is_its_outcome_whatever_the_data_says():
    for name, contact, tally, kind in [
        ('enrich_email', 'email', {'not_found': 2}, 'none_found'),
        ('enrich_email', 'email', {'failed': 2}, 'all_failed'),
        ('enrich_phone', 'phone', {'found': 1, 'failed': 1, 'not_found': 1}, 'partial'),
        ('enrich_phone', 'phone', {'found': 4, 'no_input': 1}, 'partial'),
    ]:
        outcome = _outcome(contact, **tally)
        got = summary_of(_tool(name), ToolResult(ok=True, outcome=outcome, data={'found': 99, 'searched': 99}), {})
        assert got == outcome and got.kind == kind


def test_an_empty_outcome_is_the_summary_even_when_the_call_failed():
    no_match = NoRowMatch(kind='no_row_match', contact='email')
    assert summary_of(_tool('enrich_email'), ToolResult(ok=False, error='x', outcome=no_match), {}) == no_match
    nothing = NothingToSearch(kind='nothing_to_search', contact='phone', unmatched=1)
    assert summary_of(_tool('enrich_phone'), ToolResult(ok=True, outcome=nothing), {}) == nothing


def test_a_failed_call_and_an_uncountable_tool_have_no_summary():
    assert summary_of(_tool('enrich_email'), ToolResult(ok=False, error='x'), {}) is None
    assert summary_of(_tool('web_search'), ToolResult(ok=True, data={'found': 4}), {}) is None
    assert summary_of(None, ToolResult(ok=True, data={'found': 4}), {}) is None


def _writes_rows(source: str) -> bool:
    return any(
        isinstance(node, ast.Call)
        and getattr(node.func, 'id', None) == 'ToolResult'
        and any(k.arg in ('rows', 'row_updates') for k in node.keywords)
        for node in ast.walk(ast.parse(source))
    )


def test_every_tool_that_writes_rows_names_what_it_finds():
    missing = [
        t.name
        for t in registry.all_tools()
        if (module := inspect.getmodule(t.handler))
        and module.__name__.startswith('app.')
        and _writes_rows(inspect.getsource(module))
        and not t.found_noun
    ]
    assert missing == []


def test_every_visible_tool_has_a_card_label_in_both_languages():
    import json
    from pathlib import Path

    messages = Path(__file__).parents[2] / 'web' / 'messages'
    hidden = {'ask_user', 'set_brief'}
    app_tools = {
        t.name for t in registry.all_tools() if (m := inspect.getmodule(t.handler)) and m.__name__.startswith('app.')
    }
    for lang in LOCALES:
        labels = json.loads((messages / f'{lang}.json').read_text())['chat']['tools']
        assert sorted(app_tools - hidden - labels.keys()) == [], lang
