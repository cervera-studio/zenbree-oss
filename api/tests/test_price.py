"""A paid tool's ``Price``: the units a call can deliver, the rows it bills, and the cells a charge buys."""

import re

import pytest

from app.agent.tools import handlers, registry  # noqa: F401  (handlers register the tools)
from app.billing.price import Price

_EMAIL = registry.get('enrich_email')
_PHONE = registry.get('enrich_phone')


@pytest.mark.parametrize(
    ('args', 'rows'),
    [
        ({'row_ids': ['a'], 'people': [{'row_id': f'p{i}'} for i in range(49)]}, 50),  # #418
        ({'row_ids': ['a', 'b'], 'people': [{'row_id': 'a'}, {'row_id': 'b'}]}, 2),
        ({'people': [{'name': 'no row'}]}, 50),
        ({'items': ['x', 'y', 'z']}, 50),  # enrich_email takes no items
    ],
)
def test_an_email_quote_counts_each_named_row_once(args, rows):
    quote = _EMAIL.price.quote(args, _EMAIL.input_schema)
    assert (quote.rows, quote.credits) == (rows, rows)


def test_a_phone_quote_costs_8_credits_a_row():
    quote = _PHONE.price.quote({'row_ids': ['a', 'b', 'a']}, _PHONE.input_schema)
    assert (quote.rows, quote.credits) == (2, 16)


@pytest.mark.parametrize(
    ('cells', 'found'), [({'phone': '+39 02 1234 567', 'phone_status': 'revealed'}, True), ({'phone': None}, False)]
)
def test_a_phone_is_found_when_a_number_came_back(cells, found):
    assert _PHONE.price.found(cells) is found


def test_the_billed_keys_are_the_keys_of_every_price():
    assert registry.billed_keys() == {'email', 'email_status', 'email_source', 'phone', 'phone_status', 'phone_source'}


async def _nothing(args, ctx):
    return registry.ToolResult(ok=True)


@pytest.mark.parametrize('credits', [0, -1])
def test_a_tool_cannot_register_a_price_that_charges_nothing(credits):
    tool = registry.ToolDef('fake_free', '', {}, _nothing, price=Price(credits, (), lambda cells: False))

    refusal = f"tool 'fake_free' has a Price of {credits} credits: a free tool declares no Price"
    with pytest.raises(ValueError, match=f'^{re.escape(refusal)}$'):
        registry.register(tool)

    assert registry.get('fake_free') is None
