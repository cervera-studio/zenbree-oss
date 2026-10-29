"""What a scheduled run delivers and spends (#660). A free search (its tool has no ``Price``) holds nothing. A paid one
holds its quote on a ``Tab`` at the claim; ``deliver`` stores the rows, bills the found ones from that tab and closes
it, in ``finish``'s transaction."""

from app.agent import free_rows
from app.agent.tools import registry
from app.agent.tools.registry import ToolContext
from app.billing import credits
from app.billing.price import Quote
from app.billing.tab import Tab, close_stored
from app.lists.rows import add_rows

RUN_LIMIT = 1  # credits one run may spend (owner decision, 2026-10-13)


def quote(config) -> Quote | None:
    """What the run's search may bill; None when it is free."""
    tool = registry.get(config['tool_name'])
    price = tool.price if tool else None
    return price.quote(config['tool_input'], tool.input_schema) if tool and price and price.credits else None


def fits(config) -> bool:
    """The run's search still exists and its quote is within the run's limit."""
    priced = quote(config)
    return registry.get(config['tool_name']) is not None and not (priced and priced.credits > RUN_LIMIT)


def hold(conn, run, payer: credits.Payer) -> Tab | credits.Refused | None:
    """The run's tab, holding its quote; None for a free search."""
    priced = quote(run['config'])
    if not priced:
        return None
    return Tab.open(
        conn,
        payer,
        priced.credits,
        kind='saved_search',
        reference_id=str(run['id']),
        tool_name=run['config']['tool_name'],
        description='Scheduled search',
    )


def lock(conn, run) -> Tab | None:
    """The run's tab, locked until the caller's commit so nothing closes the hold under the rows it pays for.
    HoldGone when the reaper closed it."""
    if not run['reservation_id']:
        return None
    tab = Tab.stored(conn, str(run['reservation_id']))
    tab.lock(conn)
    return tab


def deliver(conn, run, tab: Tab | None, rows: list[dict], entity: str) -> tuple[list[str], int]:
    """Stores the run's rows: ``(new row ids, credits spent)``. A paid run stores its found rows up to its quote, as
    the chat's delivery does, then bills each one that landed new and closes its tab."""
    list_id, priced = run['config']['list_id'], quote(run['config'])
    if not (tab and priced):
        return add_rows(conn, list_id, entity, rows), tab.close(conn) if tab else 0
    found = [r for r in rows if priced.price.found(r)]
    missed = add_rows(conn, list_id, entity, [r for r in rows if not priced.price.found(r)])
    billed = add_rows(conn, list_id, entity, found[: priced.rows])
    tab.record(conn, str(run['id']), list_id, billed, priced.price)
    return [*missed, *billed], tab.close(conn)


def close(conn, run) -> None:
    """Closes the run's hold from its charges, which is a release when it stored nothing."""
    if run['reservation_id']:
        close_stored(conn, str(run['reservation_id']))


async def call(tool, run):
    """The search's result, or None when the organization is out of today's free rows (the vendor is not called).
    The wallet may be empty, so only the daily allowance applies; the rows returned settle it."""
    hold = ''
    if tool.name in free_rows.SEARCHES:
        hold, refusal = free_rows.hold_page(run['organization_id'])
        if refusal:
            return None
    result = None
    try:
        config = run['config']
        result = await tool.handler(
            config['tool_input'], ToolContext(user_id=str(run['user_id']), list_id=config['list_id'])
        )
        return result
    finally:
        if hold:
            free_rows.settle(hold, len(result.rows) if result and result.ok else 0)
