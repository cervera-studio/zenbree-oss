"""Apollo's asynchronous phone job: poll it after /people/match and fold the number onto the matched person.

The number never rides the sync /people/match body. Apollo returns a top-level integer ``request_id`` for the
phone job; the hex id under ``phone_enrichment`` 404s, so the poll keys on the integer one.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from app.billing.paid_call import paid_call
from app.core.errors import Ok, Result, err, ok
from app.people.apollo_decode import _str_or_none

if TYPE_CHECKING:
    from app.people.apollo_models import PersonEnriched

logger = structlog.get_logger()

# Poll budget for the phone job, separate from the match timeout: Apollo
# typically resolves in ~4s, so poll every 3s up to a 24s ceiling. Each GET
# gets its own short HTTP timeout. These are latency/cost levers.
_PHONE_POLL_INTERVAL_SECONDS = 3.0
_PHONE_POLL_CEILING_SECONDS = 24.0
_PHONE_POLL_HTTP_TIMEOUT = 10.0
# Person statuses that say the job is still running. ponytail: no recorded Apollo body shows them; the list only keeps
# a running job from reading as "no number".
_PENDING = frozenset({'pending', 'queued', 'processing', 'in_progress', 'running'})


async def resolve_phone(
    match_resp: httpx.Response, person: PersonEnriched, base_url: str, headers: dict[str, str]
) -> Result[PersonEnriched]:
    """``person`` with the job's number. ``phone_status: not_found`` needs a finished job with no number: the web
    then offers no paid retry (#504). A missing id, or a job that failed or did not finish in time, is
    ``CONTACTS.PHONE_JOB_UNANSWERED``, a failed source, so the row can be searched again."""

    async def _reveal() -> Result[tuple[str | None, int, bool]]:
        request_id = phone_request_id(match_resp)
        if request_id is None:
            return ok((None, 0, False))
        return ok(await poll_phone_result(f'{base_url}/webhook_result/{request_id}', headers))

    revealed = await paid_call('apollo.phone_reveal', _reveal)
    # ``_reveal`` never returns Err, so the vendor cost read from a poll is recorded; the narrowing is for mypy.
    phone, _, finished = revealed.value if isinstance(revealed, Ok) else (None, 0, False)
    if phone is not None:
        return ok(replace(person, phone=phone, phone_status='revealed'))
    if not finished:
        return err('CONTACTS.PHONE_JOB_UNANSWERED', detail='the phone lookup did not finish')
    return ok(replace(person, phone_status='not_found'))


async def poll_phone_result(
    url: str,
    headers: dict[str, str],
    *,
    interval: float = _PHONE_POLL_INTERVAL_SECONDS,
    ceiling: float = _PHONE_POLL_CEILING_SECONDS,
) -> tuple[str | None, int, bool]:
    """Poll GET /webhook_result/{request_id} until the job finishes.

    Bounded by ``ceiling`` wall-clock seconds, polling every ``interval``.
    Returns the sanitized E.164 number (``None`` when the finished job has
    none), the provider-reported credits, and whether the job finished. A
    pending job, transport errors and non-200 polls (auth/rate-limit/5xx) are
    retried until the deadline, which returns ``False``: the caller reads that
    as a failed source, never as "no number" (#504). The deadline logs the
    last body's shape, so a job shape this module does not know shows in the
    logs. Runs on the event loop with the async client and asyncio.sleep, so
    it never blocks and a cancellation propagates cleanly.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + ceiling
    last_error_status: int | None = None
    last_shape: dict[str, Any] | None = None
    credits = 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(_PHONE_POLL_HTTP_TIMEOUT)) as client:
        while True:
            try:
                resp = await client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                logger.warning('apollo.phone_poll.transport_error', error=type(exc).__name__)
            else:
                if resp.status_code == 200:
                    last_error_status = None
                    credits = max(credits, phone_credits_from_poll(resp))
                    phone = phone_from_poll(resp)
                    if phone is not None or phone_job_finished(resp):
                        return phone, credits, True
                    last_shape = _shape(resp)
                else:
                    last_error_status = resp.status_code
            if loop.time() + interval >= deadline:
                if last_error_status is not None:
                    logger.warning('apollo.phone_poll.provider_error', url=url, status=last_error_status)
                else:
                    logger.warning('apollo.phone_poll.timeout', url=url, shape=last_shape)
                return None, credits, False
            await asyncio.sleep(interval)


def phone_request_id(resp: httpx.Response) -> int | None:
    """Extract the top-level integer ``request_id`` used to poll the phone job.

    ``bool`` is rejected because it is an ``int`` subclass and would be a
    nonsense request id.
    """
    payload = _json(resp)
    request_id = payload.get('request_id')
    if isinstance(request_id, bool) or not isinstance(request_id, int):
        return None
    return request_id


def phone_from_poll(resp: httpx.Response) -> str | None:
    """Parse the sanitized E.164 number out of a /webhook_result body.

    Shape: ``webhook_result.people[0].phone_numbers[0].sanitized_number``,
    falling back to ``raw_number``. Any missing/misshapen level yields None so
    an in-flight (still pending) poll simply reads as "no number yet".
    """
    numbers = _first_person(resp).get('phone_numbers')
    if not isinstance(numbers, list) or not numbers or not isinstance(numbers[0], dict):
        return None
    entry = numbers[0]
    return _str_or_none(entry.get('sanitized_number')) or _str_or_none(entry.get('raw_number'))


def phone_job_finished(resp: httpx.Response) -> bool:
    """Did this body carry the finished job: a person whose ``phone_numbers`` list is there, maybe empty, and no
    running status? Only this shape lets "Apollo has no number" become a final verdict."""
    person = _first_person(resp)
    status = str(person.get('status') or '').lower()
    return isinstance(person.get('phone_numbers'), list) and status not in _PENDING


def phone_credits_from_poll(resp: httpx.Response) -> int:
    """Read Apollo's measured phone cost without trusting non-numeric values."""
    webhook_result = _json(resp).get('webhook_result')
    raw = webhook_result.get('credits_consumed', 0) if isinstance(webhook_result, dict) else 0
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0 else 0


def _shape(resp: httpx.Response) -> dict[str, Any]:
    """Keys and statuses only, never values: the body holds a person's data."""
    webhook_result = _json(resp).get('webhook_result')
    people = webhook_result.get('people') if isinstance(webhook_result, dict) else None
    person = _first_person(resp)
    return {
        'webhook_result': sorted(webhook_result) if isinstance(webhook_result, dict) else type(webhook_result).__name__,
        'people': len(people) if isinstance(people, list) else None,
        'person': sorted(person),
        'status': _str_or_none(person.get('status')),
    }


def _first_person(resp: httpx.Response) -> dict[str, Any]:
    webhook_result = _json(resp).get('webhook_result')
    people = webhook_result.get('people') if isinstance(webhook_result, dict) else None
    first = people[0] if isinstance(people, list) and people else None
    return first if isinstance(first, dict) else {}


def _json(resp: httpx.Response) -> dict[str, Any]:
    try:
        payload = resp.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}
