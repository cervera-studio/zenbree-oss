"""One door for every paid provider call (#2306).

Attribution used to be opt-in. Each client method decided at its own call
site whether to log what the call cost and what the number was, which is
why Apollo consumption was attributed for 3.6% (#2049): four of the five
paths simply never wrote the keyword. The number itself drifted too —
``search_organizations`` and ``discover_organizations`` hit the SAME
endpoint and used to disagree about whether a zero-row page costs a credit.

:func:`paid_call` runs the provider call and writes the attribution in the
same place, so the two cannot come apart: no result without a cost line,
no cost line without a result. The price is deliberately NOT a caller
argument — it lives in the vendor's tariffs (``app.billing.vendors``), keyed
by operation, which is what turns "empty → not billed" into a property of the operation instead
of a line someone remembered to write. An operation with no tariff raises:
a paid call nobody costed must not ship as a silent zero.

The seat that paid rides along for free: ``app.core.logging_config``
injects ``user_id`` into every structlog event, so the ``.end`` line this
emits is already attributed without threading an id through the client.

Failure never bills. An ``Err`` from ``call`` logs zero credits whatever
the tariff says, which is the one half of #1423 ("free if not found") that
holds for every provider.

The idempotency half of #2306 is not here yet: it belongs on this seam —
a key handed to :func:`paid_call`, a replayed result logged at zero — and
nothing passes one today, so the parameter would be a promise rather than
a mechanism. See the issue for the plan. Treg is the shape it should take,
from the far side: the vendor holds the key, answers a replay without
charging, and the tariff reads that back as zero.

Not every provider meters in credits. Treg bills a prepaid balance in
micro-dollars and reports what each call took in a response header, so its
tariff reads the vendor's own number and its ``Vendor.meter`` names it ``treg_cost_micro`` —
one price per operation, still no number at the call site.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

import structlog

from app.billing.cost_scope import cost_scope
from app.billing.vendors import VENDORS, Vendor
from app.core.db import get_conn
from app.core.errors import Err

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.core.errors import Result

logger = structlog.get_logger()


#: Who pays for the vendor calls made in this context. Tool handlers set it
#: from ``ToolContext.user_id``; unset → the ``vendor_calls`` row has no user.
current_user_id: ContextVar[str | None] = ContextVar('vendor_user_id', default=None)

#: The vendors called in this context, in order: the agent loop sets a fresh list around each
#: tool call, so the transcript can say which vendors answered it (#352).
vendor_trail: ContextVar[list[str] | None] = ContextVar('vendor_trail', default=None)


def record_vendor_call(vendor: Vendor, operation: str, *, metered: float, units: int = 1, success: bool = True) -> None:
    """Insert one ``vendor_calls`` row for ``<vendor>.<operation>``, priced at ``metered`` units of the vendor's
    meter. Sync; never raises (a cost line must not break a tool)."""
    if (trail := vendor_trail.get()) is not None and vendor.name not in trail:
        trail.append(vendor.name)
    name = f'{vendor.name}.{operation}'
    try:
        with cost_scope(), get_conn(timeout=2) as conn:
            conn.execute(
                'INSERT INTO vendor_calls (user_id, vendor, operation, cost_usd, units, success)'
                ' VALUES (%s, %s, %s, %s, %s, %s)',
                (current_user_id.get(), vendor.name, name, round(metered * vendor.usd_per_unit, 6), units, success),
            )
    except Exception as e:
        logger.warning('vendor_calls.write_failed', vendor=vendor.name, operation=name, error=str(e)[:200])


def record_lookup(provider: str, channel: str, found: bool) -> None:
    """Insert one ``contact_lookups`` row (per-provider hit rate). Sync; never raises."""
    try:
        with cost_scope(), get_conn(timeout=2) as conn:
            conn.execute(
                'INSERT INTO contact_lookups (user_id, provider, channel, found) VALUES (%s, %s, %s, %s)',
                (current_user_id.get(), provider, channel, found),
            )
    except Exception as e:
        logger.warning('contact_lookups.write_failed', provider=provider, error=str(e)[:200])


async def paid_call[T](
    operation: str,
    call: Callable[[], Awaitable[Result[T]]],
    **fields: Any,
) -> Result[T]:
    """Run ``call`` and attribute what it cost, atomically.

    ``operation`` is ``<provider>.<name>``; ``fields`` are the request-side
    values that belong on the line whether the call succeeded or not (the
    domain looked up, the org id asked for). Result-side values come from
    the tariff, so nobody has to remember them per call site.
    """
    vendor_name, name = operation.split('.', 1)
    vendor = VENDORS[vendor_name]
    tariff = vendor.tariffs[name]
    credits_key = vendor.meter_key
    result = await call()
    if isinstance(result, Err):
        logger.warning(
            f'{operation}.end',
            **{credits_key: 0, 'error_code': result.error.code, **fields},
        )
        await asyncio.to_thread(record_vendor_call, vendor, name, metered=0, success=False)
        return result
    credits = tariff.credits(result.value)
    logger.info(
        f'{operation}.end',
        **{
            credits_key: credits,
            **tariff.fields(result.value),
            **fields,
        },
    )
    await asyncio.to_thread(record_vendor_call, vendor, name, metered=credits)
    return result
