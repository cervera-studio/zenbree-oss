"""HubSpot read gate (E13.1 #120): check the CRM before a campaign writes to someone.

No polling (product rule on #120): HubSpot is read only when an action needs it. Each dispatcher beat
hands :func:`check` the people a step is due for now (``dispatcher.gate_candidates``); it batch-reads
them and stores one verdict per person in ``crm_gate_checks``. ``dispatcher.plan`` then claims only for
people read clear in that same beat, so a missing verdict, a failed read or a block all hold the person
(fail-closed). A blocked person is not read again: a cooldown leaves the block when ``hold_until``
passes, any other block when a HubSpot webhook updates the verdict.

Rules, first match wins (decisions on #120):
- opt-out: the contact unsubscribed from all email (``hs_email_optout``). Lasting.
- customer: the contact's primary company has lifecycle stage ``customer``.
- open deal: that company has a deal not closed won or lost (``hs_num_open_deals``).
- owned: the contact or that company has a HubSpot owner who is not the connected user.
- cooldown: the contact's ``notes_last_contacted`` (HubSpot "Last contacted": the last logged sales
  email, call or meeting) is within :data:`COOLDOWN`; lapses on its own. A touch Zenbree logged itself
  (write-back, ``app.crm.activities``) is not one: only a later contact by someone else holds (#528).
Customer, open deal and owned hold until HubSpot says otherwise (webhook), not for good.

People are matched by email only: a LinkedIn-only target has no HubSpot identity to check.
"""

from __future__ import annotations

import contextlib
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import redis
import structlog

from app.admin.settings import is_disabled
from app.core.config import settings
from app.core.db import get_conn
from app.core.errors import Result, is_err
from app.crm import activities, hubspot_client
from app.integrations.providers import HUBSPOT, OAuthProvider

logger = structlog.get_logger()

AccessToken = Callable[[dict, OAuthProvider], Awaitable[str | None]]

# fixed for every user; per-user when a customer asks for it.
COOLDOWN = timedelta(days=30)
COOLDOWN_REASON = 'HUBSPOT_COOLDOWN'
OPTED_OUT_REASON = 'HUBSPOT_OPTED_OUT'
CUSTOMER_REASON = 'HUBSPOT_CUSTOMER'
OPEN_DEAL_REASON = 'HUBSPOT_OPEN_DEAL'
OWNED_REASON = 'HUBSPOT_OWNED'

# A failed read is retried from the queue (Celery ``countdown``), never by the dispatcher beat: 1, 2,
# 4… minutes up to an hour, and at least HubSpot's ``Retry-After`` on a 429.
RETRY_BASE_SECONDS = 60
RETRY_MAX_SECONDS = 3600
# While a retry is queued the beat leaves that owner alone. The mark outlives the countdown by this
# much, so a lost task (worker killed) cannot hold the owner's people forever.
RETRY_MARGIN_SECONDS = 900

ScheduleRetry = Callable[[str, int], None]  # (owner id, delay in seconds)


class ReadFailed(Exception):
    """HubSpot could not be read. ``permanent`` (401/403: revoked grant, missing scope) is never
    retried: the connection goes to ``error`` and waits for the user to reconnect."""

    def __init__(self, code: str, retry_after: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after

    @property
    def permanent(self) -> bool:
        return self.code == 'HUBSPOT.AUTH'


def retry_delay(attempt: int, retry_after: int | None = None) -> int:
    """Seconds before retry number ``attempt`` (0-based)."""
    return max(min(RETRY_BASE_SECONDS * 2**attempt, RETRY_MAX_SECONDS), retry_after or 0)


def parse_datetime(value: object) -> datetime | None:
    """A HubSpot datetime property: ISO 8601, or epoch milliseconds on older payloads."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.isdigit():
        return datetime.fromtimestamp(int(raw) / 1000, tz=UTC)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _open_deals(company: dict) -> bool:
    try:
        return float(company.get('hs_num_open_deals') or 0) > 0
    except TypeError, ValueError:
        return False


def _owned_by_another(record: dict | None, my_owner_id: str | None) -> bool:
    owner = str((record or {}).get('hubspot_owner_id') or '').strip()
    return bool(owner) and owner != (my_owner_id or '')


def verdict(
    contact: dict | None,
    company: dict | None,
    my_owner_id: str | None,
    now: datetime,
    own_touch: datetime | None = None,
) -> tuple[str | None, datetime | None]:
    """``(reason, hold_until)`` for one person; ``(None, None)`` when nothing blocks them.
    ``contact`` is None when HubSpot has no contact with that email; ``company`` is the contact's
    primary company, None without one; ``my_owner_id`` is the connected user's own owner id;
    ``own_touch`` is the latest touch Zenbree logged on the contact's timeline."""
    if contact is None:
        return None, None
    if str(contact.get('hs_email_optout') or '').lower() == 'true':
        return OPTED_OUT_REASON, None
    if company and str(company.get('lifecyclestage') or '').lower() == 'customer':
        return CUSTOMER_REASON, None
    if company and _open_deals(company):
        return OPEN_DEAL_REASON, None
    if _owned_by_another(contact, my_owner_id) or _owned_by_another(company, my_owner_id):
        return OWNED_REASON, None
    last = parse_datetime(contact.get('notes_last_contacted'))
    # HubSpot keeps the logged time to the millisecond, the database to the microsecond.
    ours = own_touch is not None and last is not None and last <= own_touch + timedelta(seconds=1)
    if last and last + COOLDOWN > now and not ours:
        return COOLDOWN_REASON, last + COOLDOWN
    return None, None


def _redis() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)


def _waiting(owner_id: str) -> bool:
    try:
        return bool(_redis().exists(f'crm:gate:retrying:{owner_id}'))
    except redis.RedisError:
        return False  # without Redis the read is simply tried


def wait_for_retry(owner_id: str, delay: int) -> None:
    """A retry is queued ``delay`` seconds from now: until it ran, the beat does not read this owner."""
    with contextlib.suppress(redis.RedisError):
        _redis().set(f'crm:gate:retrying:{owner_id}', 1, ex=delay + RETRY_MARGIN_SECONDS)


def resume(owner_id: str) -> None:
    with contextlib.suppress(redis.RedisError):
        _redis().delete(f'crm:gate:retrying:{owner_id}')


def mark_error(connection: dict, detail: str) -> None:
    """HubSpot refused the grant: the settings page offers Reconnect, and reconnecting re-evaluates
    everyone. Until then the dispatcher holds this owner's people. The condition sits in the SET, so 0 rows means a
    scope row-level security refused with no error (not the owner's): logged, as the people stay held either way."""
    with get_conn() as conn:
        written = conn.execute(
            "UPDATE user_oauth_connections SET status_detail = CASE WHEN status = 'connected' THEN %s"
            " ELSE status_detail END, status = CASE WHEN status = 'connected' THEN 'error' ELSE status END"
            ' WHERE id = %s',
            (detail[:500], connection['id']),
        ).rowcount
    if not written:
        logger.error('crm.gate.connection_error_not_stored', user_id=str(connection['user_id']))
    logger.warning('crm.gate.connection_error', user_id=str(connection['user_id']), detail=detail)


def _connections(owner_ids: list[str]) -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM user_oauth_connections WHERE provider = 'hubspot' AND status = 'connected'"
            ' AND user_id = ANY(%s::uuid[])',
            (owner_ids,),
        ).fetchall()


def _company_id(contact: dict | None) -> str | None:
    value = str((contact or {}).get('associatedcompanyid') or '').strip()
    return value or None


def _store(owner_id: str, rows: list[tuple], now: datetime) -> None:
    """``rows``: (email, reason, hold_until, contact id, company id)."""
    with get_conn() as conn:
        for email, reason, hold_until, contact_id, company_id in rows:
            conn.execute(
                """INSERT INTO crm_gate_checks
                       (owner_id, email, reason, hold_until, contact_id, company_id, checked_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (owner_id, email) DO UPDATE SET
                       reason = EXCLUDED.reason, hold_until = EXCLUDED.hold_until,
                       contact_id = EXCLUDED.contact_id, company_id = EXCLUDED.company_id,
                       checked_at = EXCLUDED.checked_at""",
                (owner_id, email, reason, hold_until, contact_id, company_id, now),
            )


def unwrap[T](result: Result[T]) -> T:
    if is_err(result):
        raise ReadFailed(result.error.code, result.error.context.get('retry_after'))
    return result.value


async def evaluate(connection: dict, token: str, emails: list[str], now: datetime) -> list[tuple]:
    """Read ``emails`` (contacts, then their primary companies) and store their verdicts.
    Raises :class:`ReadFailed`, with nothing stored, when either read failed."""
    contacts = unwrap(await hubspot_client.read_contacts(token, emails))
    company_ids = sorted({cid for c in contacts.values() if (cid := _company_id(c))})
    companies = unwrap(await hubspot_client.read_companies(token, company_ids)) if company_ids else {}
    own = activities.own_touches(str(connection['user_id']), emails)
    me = connection.get('provider_owner_id')
    rows = []
    for email in emails:
        contact = contacts.get(email)
        company_id = _company_id(contact)
        reason, hold_until = verdict(contact, companies.get(company_id or ''), me, now, own.get(email))
        rows.append((email, reason, hold_until, (contact or {}).get('id'), company_id))
    _store(str(connection['user_id']), rows, now)
    return rows


def _still_connected(connection: dict) -> bool:
    with get_conn() as conn:
        return bool(
            conn.execute(
                "SELECT 1 FROM user_oauth_connections WHERE id = %s AND status = 'connected'", (connection['id'],)
            ).fetchone()
        )


async def _check_owner(
    connection: dict, emails: list[str], access_token: AccessToken, now: datetime, counts: Counter
) -> None:
    token = await access_token(connection, HUBSPOT)
    if token is None:  # not available now (revoked, unreadable or a provider failure): its people stay held
        counts['token_failed'] += 1
        return
    for start in range(0, len(emails), hubspot_client.BATCH_SIZE):
        batch = emails[start : start + hubspot_client.BATCH_SIZE]
        rows = await evaluate(connection, token, batch, now)
        counts['checked'] += len(batch)
        if held := sum(1 for row in rows if row[1]):
            counts['held'] += held


def _failed(connection: dict, failure: ReadFailed, schedule_retry: ScheduleRetry) -> None:
    owner = str(connection['user_id'])
    logger.warning('crm.gate.read_failed', user_id=owner, code=failure.code)
    if failure.permanent:
        mark_error(connection, failure.code)
        return
    delay = retry_delay(0, failure.retry_after)
    wait_for_retry(owner, delay)
    try:
        schedule_retry(owner, delay)
    except Exception as exc:  # no queue: the next beat is the retry
        logger.error('crm.gate.retry_not_queued', user_id=owner, error_type=type(exc).__name__)
        resume(owner)


async def check(
    people: dict[str, list[str]], now: datetime, access_token: AccessToken, schedule_retry: ScheduleRetry
) -> Counter:
    """Read HubSpot for ``people`` (owner id → emails owed a step now) and store their verdicts with
    ``checked_at = now``. Never raises: a failure only leaves people without a claim. A transient
    failure queues a retry (``schedule_retry``) and the owner is left alone until it ran; a refused
    grant marks the connection ``error`` and is not retried."""
    counts: Counter = Counter()
    if not people or is_disabled('vendor_disabled:hubspot'):
        return counts
    for connection in _connections(list(people)):
        owner = str(connection['user_id'])
        if _waiting(owner):
            counts['waiting'] += 1
            continue
        try:
            await _check_owner(connection, people[owner], access_token, now, counts)
        except ReadFailed as failure:
            counts['errors'] += 1
            _failed(connection, failure, schedule_retry)
        except Exception as exc:
            logger.error('crm.gate.owner_failed', user_id=owner, error_type=type(exc).__name__)
            counts['errors'] += 1
    return counts


async def retry(owner_id: str, emails: list[str], now: datetime, access_token: AccessToken) -> Counter:
    """The queued retry of a failed read, for the people of ``owner_id`` owed a step now. Raises
    :class:`ReadFailed` when HubSpot still cannot be read (the task re-queues itself, later); on a
    good read, a refused grant or nothing left to read, the beat takes over again."""
    counts: Counter = Counter()
    connections = _connections([owner_id])
    if not connections or not emails:
        resume(owner_id)
        return counts
    if is_disabled('vendor_disabled:hubspot'):
        raise ReadFailed('HUBSPOT.DISABLED')
    try:
        await _check_owner(connections[0], emails, access_token, now, counts)
    except ReadFailed as failure:
        if not failure.permanent:
            raise
        mark_error(connections[0], failure.code)
    resume(owner_id)
    return counts


def token_unavailable(connection: dict, counts: Counter) -> None:
    """``None`` = no token now (revoked, unreadable or a provider failure); the people stay held. A connection that
    is still connected is an outage: raise so the delivery is retried. One in error waits for Reconnect."""
    counts['token_failed'] += 1
    if _still_connected(connection):
        raise ReadFailed('HUBSPOT.TOKEN_UNAVAILABLE')


def opted_out_rules(conn, owner_id: str) -> frozenset[tuple[str, str]]:
    """The people HubSpot says unsubscribed, as exclusion rules (``app.exclusions.rules.Rules``), while
    the owner's HubSpot is connected: enrollment skips them like the owner's own exclusions."""
    rows = conn.execute(
        """SELECT g.email FROM crm_gate_checks g
           WHERE g.owner_id = %s AND g.reason = %s
             AND EXISTS (SELECT 1 FROM user_oauth_connections h WHERE h.user_id = g.owner_id
                         AND h.provider = 'hubspot' AND h.status IN ('connected', 'error'))""",
        (owner_id, OPTED_OUT_REASON),
    ).fetchall()
    return frozenset(('email', r['email']) for r in rows)
