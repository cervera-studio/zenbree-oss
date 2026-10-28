"""Every registered tool that writes a contact lookup answers its empty paths with a typed outcome.

The guard in ``ToolResult.__post_init__`` makes a handler raise when it writes email_status or phone_status without an
outcome. The handlers are found in the source, by the empty-path helpers they call, so a new contact tool is driven
too, whatever its ``found_noun``."""

import ast
import inspect
from pathlib import Path

import pytest

from app.agent.tools import handlers, registry
from app.agent.tools.registry import ToolContext
from app.lists.lookup_outcome import NoRowMatch, NothingToSearch
from tests.web_fixtures import web_fixture

CTX = ToolContext(user_id='00000000-0000-0000-0000-000000000001', list_id='l1')
EMPTY_PATHS = {'no_row_match', 'nothing_to_search'}


def _is_contact_module(tree: ast.AST) -> bool:
    return any(isinstance(node, ast.Call) and getattr(node.func, 'id', None) in EMPTY_PATHS for node in ast.walk(tree))


def _contact_tools() -> list[str]:
    modules = {
        f'app.agent.tools.handlers.{path.stem}'
        for path in Path(handlers.__file__).parent.glob('*.py')
        if path.stem != '_rows' and _is_contact_module(ast.parse(path.read_text()))
    }
    return sorted(
        t.name for t in registry.all_tools() if (module := inspect.getmodule(t.handler)) and module.__name__ in modules
    )


CONTACT_TOOLS = _contact_tools()


def test_the_contact_tools_are_found():
    assert CONTACT_TOOLS == ['enrich_email', 'enrich_phone']


@pytest.fixture(params=CONTACT_TOOLS)
def tool(request, monkeypatch):
    for name in ('enrich_email', 'enrich_phone'):
        monkeypatch.setattr(f'app.agent.tools.handlers.{name}.load_rows', lambda list_id, row_ids: [])
    return registry.get(request.param)


def _contact(tool: registry.ToolDef) -> str:
    return tool.name.removeprefix('enrich_')


@pytest.mark.web_fixture
async def test_ids_that_match_no_row_carry_no_row_match(tool):
    res = await tool.handler({'row_ids': ['3f2a8e4c-0000-4000-8000-000000000000']}, CTX)
    assert not res.ok and not res.row_updates
    assert res.outcome == NoRowMatch(kind='no_row_match', contact=_contact(tool))
    web_fixture(f'summary-{_contact(tool)}-no-row-match', res.outcome.model_dump(mode='json'))


@pytest.mark.web_fixture
async def test_rows_that_already_have_the_value_carry_nothing_to_search(tool, monkeypatch):
    has = {'row_id': '3f2a8e4c-0000-4000-8000-000000000001', 'email': 'a@x.it', 'phone': '+39 02 0000'}
    for name in ('enrich_email', 'enrich_phone'):
        monkeypatch.setattr(f'app.agent.tools.handlers.{name}.load_rows', lambda list_id, row_ids: [has])
    res = await tool.handler({'row_ids': [has['row_id'], '3f2a8e4c-0000-4000-8000-000000000002']}, CTX)
    assert res.ok and not res.row_updates
    assert res.outcome == NothingToSearch(kind='nothing_to_search', contact=_contact(tool), unmatched=1)
    web_fixture(f'summary-{_contact(tool)}-nothing-to-search', res.outcome.model_dump(mode='json'))
