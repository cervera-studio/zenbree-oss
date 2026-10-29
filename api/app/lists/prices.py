"""What list work costs in credits, read from the tools and the AI column fill that charge it, so the web shows
the prices the API bills and keeps no copy of them (#660, #569)."""

from functools import cache

from pydantic import BaseModel

import app.agent.tools.handlers  # noqa: F401  registers the tools priced here
from app.agent.tools import registry
from app.lists.ai_columns import AI_CELL, MAX_CELLS_PER_RUN


class CreditPrices(BaseModel):
    search: int  # a row more_rows adds, as the search behind it
    email: int  # an email found
    phone: int  # a phone number found
    ai_cell: int  # an AI column cell filled
    ai_cells_per_run: int  # the most cells one AI column fill researches


def _cost(name: str) -> int:
    tool = registry.get(name)
    if tool is None:
        raise LookupError(f'tool {name!r} is not registered')
    return tool.price.credits if tool.price else 0


@cache
def credit_prices() -> CreditPrices:
    return CreditPrices(
        search=_cost('more_rows'),
        email=_cost('enrich_email'),
        phone=_cost('enrich_phone'),
        ai_cell=AI_CELL.credits,
        ai_cells_per_run=MAX_CELLS_PER_RUN,
    )
