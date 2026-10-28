"""The bounces and replies a sending mailbox receives, pushed by its provider (#747, #1165): Gmail ``users.watch``
through a Pub/Sub topic, Microsoft Graph change notifications on the inbox. No polling: a push queues
``read_mailbox_bounces``, which reads the new messages' headers and fetches whole only the delivery-status
notifications (``app.outreach.dsn``) and the replies to our sends (``app.outreach.mailbox_replies``). Every other
message stays unread and nothing of it is stored.

A watch lasts 7 days on both. ``app.integrations.mailbox_watch.keep_watching`` starts or renews one before a send once less than ``RENEW_WHEN_LEFT``
is left, so a mailbox that sends is always heard.
ponytail: a mailbox idle for 7 days stops hearing until its next send, so a bounce arriving after that is lost; a
beat renewal is the upgrade if late soft bounces ever matter (a hard bounce comes back within minutes).

Gmail's push names only the mailbox address, so the route matches it to its connections as
``System(MAILBOX_PUSH)``. Graph's names its subscription: each one gets a notification URL naming its connection and
organization, signed by ``clientState``, so that route reads nothing before it queues for that organization.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import html
import json
import time
from hashlib import sha256

import httpx
import structlog
from celery.exceptions import SoftTimeLimitExceeded
from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import PlainTextResponse

from app.auth.middleware import public
from app.core import tasks
from app.core.config import settings
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.core.system_reasons import SystemReason
from app.integrations.mailbox_fetch import GMAIL_API, Meta, whole
from app.integrations.mailbox_fetch import meta as read_meta
from app.integrations.providers import MAILBOX_PROVIDERS
from app.outreach import bounces, dsn, mailbox_replies
from app.people.email_verification import millionverifier_status

logger = structlog.get_logger()
router = APIRouter(prefix='/v1/integrations/mailboxes', include_in_schema=False)

MAX_MESSAGES = 100  # read per push; a bigger burst is a mailbox in use for more than sending
MAX_DSN_BYTES = 1_000_000  # a report quotes one short email; anything bigger is not one of ours
# Under the task's 50 s soft limit: a read that stops at the deadline still stores how far it got.
MESSAGE_SECONDS = 12
READ_SECONDS = 30
_TIMEOUT_SECONDS = 15.0
TASK = 'read_mailbox_bounces'
# The paid re-verification of a hard bounce's address before a refund; past it, no refund.
VERIFY_SECONDS = 15
STORE_RETRIES = 3


def gmail_on() -> bool:
    return bool(settings.gmail_push_topic and settings.gmail_push_token)


def graph_on() -> bool:
    return bool(settings.api_public_base_url and settings.outreach_reply_secret)


def client_state(connection_id: str, org_id: str) -> str:
    key = settings.outreach_reply_secret.encode()
    return hmac.new(key, f'mailbox-push:{connection_id}:{org_id}'.encode(), sha256).hexdigest()


def provider_http(access_token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, headers={'Authorization': f'Bearer {access_token}'})


# ── reading ────────────────────────────────────────────────────────────────


async def _gmail_ids(http: httpx.AsyncClient, mb: dict) -> tuple[list[tuple[str, list[str]]], str | None]:
    """The history records added to the inbox after the stored history id, each with its message ids, oldest first,
    and where the next read starts once they are all handled: Gmail's current history id, or the last record here
    when there are more than ``MAX_MESSAGES`` (the rest on the next push)."""
    start = mb['inbound_history_id']
    if not start:
        return [], None
    records: list[tuple[str, list[str]]] = []
    params = {'startHistoryId': start, 'historyTypes': 'messageAdded', 'labelId': 'INBOX'}
    while True:
        resp = await http.get(f'{GMAIL_API}/history', params=params)
        if resp.status_code == 404:  # older than Gmail keeps: start again from now
            resp = await http.get(f'{GMAIL_API}/profile')
            return records, str(resp.json()['historyId'])
        resp.raise_for_status()
        body = resp.json()
        records += [
            (str(h['id']), [m['message']['id'] for m in h.get('messagesAdded', [])]) for h in body.get('history', [])
        ]
        if not body.get('nextPageToken'):
            return records, str(body['historyId'])
        if sum(len(m) for _, m in records) >= MAX_MESSAGES:
            return records, records[-1][0]
        params['pageToken'] = body['nextPageToken']


class StoreFailed(Exception):
    """A bounce that matched a send could not be stored: the task retries the read from before its message."""


async def _reverified_invalid(mb: dict, bounce: dsn.Bounce) -> bool:
    """Owner decision on #919: a hard bounce refunds only when MillionVerifier, asked again now, calls the address
    invalid; a bounce report alone can be planted. Paid only for a bounce that could refund (``bounces.may_refund``).
    No verdict (no key, switched off, an error, too slow), ``verified`` or ``catch_all`` refund nothing."""
    if not await asyncio.to_thread(bounces.may_refund, str(mb['id']), bounce):
        return False
    try:
        status = await asyncio.wait_for(millionverifier_status(bounce.recipient), VERIFY_SECONDS)
    except TimeoutError:
        status = None
    logger.info('campaign.bounce.reverified', connection_id=str(mb['id']), verdict=status or 'none')
    return status == 'invalid'


async def _handle(http: httpx.AsyncClient, mb: dict, message_id: str, *, last_try: bool) -> int:
    """One message: fetched, read and recorded. A failure to fetch or parse that one message (a provider error, a
    report the parser chokes on, a slow body) is logged and skipped: it never stops the others or holds the cursor
    back. Recording is not inside that: a bounce that matched a send and could not be stored raises ``StoreFailed``,
    so the read stops before it and is retried; on the task's last try it is logged at error level and passed, so a
    failure that recurs every time (a CHECK violation, a bug) never pins the cursor. The time limit is re-raised."""
    reply: Meta | None = None  # no report: whether it is a reply is the replies module's to decide
    try:
        meta = await asyncio.wait_for(read_meta(http, mb, message_id), MESSAGE_SECONDS)
        raw = None
        if meta and not dsn.looks_like_dsn(meta[0]):
            reply = meta
        elif meta and meta[1] <= MAX_DSN_BYTES:
            raw = await asyncio.wait_for(whole(http, mb, message_id, MAX_DSN_BYTES), MESSAGE_SECONDS)
        found, refused = (
            dsn.read(raw, provider=mb['provider'], mailbox=mb['account_label'] or '') if raw else ([], None)
        )
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:
        logger.warning(
            'mailbox_push.dsn_unparsable',
            message_id=message_id,
            connection_id=str(mb['id']),
            error_type=type(exc).__name__,
        )
        return 0
    if refused:
        logger.warning('mailbox_push.dsn_rejected', connection_id=str(mb['id']), reason=refused)
    try:
        recorded = 0
        for bounce in found:
            invalid = await _reverified_invalid(mb, bounce)
            recorded += bounces.record(str(mb['id']), bounce, reverified_invalid=invalid)
        if reply:
            recorded += await mailbox_replies.handle(
                mb, message_id, *reply, whole=lambda limit: whole(http, mb, message_id, limit)
            )
        return recorded
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:
        if not last_try:
            raise StoreFailed(type(exc).__name__) from exc
        logger.error(
            'mailbox_push.store_failed',
            message_id=message_id,
            connection_id=str(mb['id']),
            error_type=type(exc).__name__,
        )
        return 0


def _now() -> float:
    return time.monotonic()


def _store_cursor(mb: dict, cursor: str | None) -> None:
    if cursor and cursor != mb['inbound_history_id']:
        with get_conn() as conn:
            conn.execute('UPDATE user_oauth_connections SET inbound_history_id = %s WHERE id = %s', (cursor, mb['id']))


async def _read_graph(
    http: httpx.AsyncClient, mb: dict, message_ids: list[str], deadline: float, last_try: bool
) -> int:
    recorded = 0
    for n, message_id in enumerate(message_ids):
        if _now() > deadline:  # out of time: the rest go to a new read of their own
            _requeue(mb, message_ids[n:])
            break
        recorded += await _handle(http, mb, message_id, last_try=last_try)
    return recorded


async def _read_gmail(http: httpx.AsyncClient, mb: dict, deadline: float, last_try: bool) -> int:
    records, current = await _gmail_ids(http, mb)  # a provider error here leaves the cursor: nothing was handled
    recorded, done = 0, None  # done: the last record whose messages were all handled
    try:
        for record_id, message_ids in records:
            if _now() > deadline:  # out of time: the next push reads on after the last record handled
                current = done
                break
            for message_id in message_ids:
                recorded += await _handle(http, mb, message_id, last_try=last_try)
            done = record_id
    except BaseException:
        _store_cursor(mb, done)  # a bounce that could not be stored, or the time limit: retried from its record
        raise
    _store_cursor(mb, current)
    return recorded


async def read(connection_id: str, message_ids: list[str], *, last_try: bool = True) -> int:
    """Record the bounces among a mailbox's new messages (Gmail: after its history id; Graph: ``message_ids``).
    ``last_try``: the task will not retry, so a bounce that cannot be stored is passed rather than raised."""
    from app.outreach.dispatcher import _access_token  # the dispatcher imports this module

    with get_conn() as conn:
        mb = conn.execute(
            "SELECT * FROM user_oauth_connections WHERE id = %s AND status = 'connected'"
            " AND provider IN ('google', 'microsoft')",
            (connection_id,),
        ).fetchone()
    if not mb or not MAILBOX_PROVIDERS[mb['provider']].grants_read(mb['granted_scopes'] or []):
        return 0
    access_token = await _access_token(mb, MAILBOX_PROVIDERS[mb['provider']])
    if access_token is None:
        return 0
    deadline = _now() + READ_SECONDS
    async with provider_http(access_token) as http:
        if mb['provider'] == 'google':
            return await _read_gmail(http, mb, deadline, last_try)
        return await _read_graph(http, mb, message_ids[:MAX_MESSAGES], deadline, last_try)


def register(org_task) -> None:
    """The task, declared here and registered by app/worker.py."""

    # A bounce that matched and could not be stored is read again: Gmail from the record before it,
    # Graph from the same ids, since Graph never notifies twice. Recording is idempotent.
    @org_task(
        name=TASK,
        soft_time_limit=50,
        time_limit=55,
        bind=True,
        autoretry_for=(StoreFailed,),
        max_retries=STORE_RETRIES,
        retry_backoff=30,
    )
    def read_mailbox_bounces(self, connection_id: str, message_ids: list[str]) -> int:
        last_try = self.request.retries >= STORE_RETRIES
        return asyncio.run(read(connection_id, message_ids, last_try=last_try))


def _requeue(mb: dict, message_ids: list[str]) -> None:
    from app.worker import celery_app

    try:  # in the task, as the mailbox's organization
        celery_app.send_task(TASK, args=[str(mb['id']), message_ids])
    except Exception as exc:
        logger.error('mailbox_push.requeue_failed', count=len(message_ids), error_type=type(exc).__name__)


def _queue(org_id: str, connection_id: str, message_ids: list[str]) -> None:
    from app.worker import celery_app

    try:
        with tasks.queued_for(org_id, 'mailbox push'):
            celery_app.send_task(TASK, args=[connection_id, message_ids])
    except Exception as exc:  # the provider retries a push we fail
        logger.error('mailbox_push.enqueue_failed', error_type=type(exc).__name__)
        raise api_error(503, 'INTEGRATIONS.UNAVAILABLE', 'try again later') from None


# ── routes ─────────────────────────────────────────────────────────────────


def _gmail_connections(address: str) -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT id::text, organization_id::text FROM user_oauth_connections WHERE provider = 'google'"
            " AND status = 'connected' AND lower(account_label) = lower(%s) AND inbound_subscription_expires_at > now()",
            (address,),
        ).fetchall()


@router.post('/google/push', status_code=204, dependencies=[public(SystemReason.MAILBOX_PUSH)])
async def gmail_push(request: Request, token: str = Query(default='', max_length=200)) -> Response:
    """Pub/Sub's push of a Gmail watch: ``message.data`` is base64 JSON ``{emailAddress, historyId}``."""
    if not gmail_on() or not hmac.compare_digest(token.encode(), settings.gmail_push_token.encode()):
        raise api_error(401, 'INTEGRATIONS.WEBHOOK_UNAUTHORIZED', 'invalid push token')
    try:
        data = json.loads(base64.b64decode((await request.json())['message']['data']))
        address = str(data['emailAddress'])
    except ValueError, KeyError, TypeError, binascii.Error:
        return Response(status_code=204)  # acknowledged: Pub/Sub would redeliver a malformed one forever
    for row in await asyncio.to_thread(_gmail_connections, address):
        _queue(row['organization_id'], row['id'], [])
    return Response(status_code=204)


@router.post('/microsoft/push', dependencies=[public(None)])
async def graph_push(
    request: Request,
    c: str = Query(default='', max_length=40),
    o: str = Query(default='', max_length=40),
    validation_token: str = Query(default='', alias='validationToken', max_length=512),
) -> Response:
    """Graph's notifications of the subscription ``mailbox_watch.keep_watching`` made for connection ``c`` of organization ``o``.
    On creation Graph first checks the URL with an empty POST: it must answer ``validationToken`` back as plain text.
    Only that shape is answered, escaped and never sniffed as HTML: the URL is open to anyone."""
    if validation_token and graph_on() and not await request.body():
        return PlainTextResponse(html.escape(validation_token), headers={'X-Content-Type-Options': 'nosniff'})
    if not graph_on() or validation_token:
        raise api_error(401, 'INTEGRATIONS.WEBHOOK_UNAUTHORIZED', 'invalid client state')
    expected = client_state(c, o)
    try:
        notes = (await request.json())['value']
        ids = [
            str(n['resourceData']['id'])
            for n in notes
            if hmac.compare_digest(str(n.get('clientState', '')).encode(), expected.encode())
        ]
    except ValueError, KeyError, TypeError:
        return Response(status_code=202)
    if not ids:
        raise api_error(401, 'INTEGRATIONS.WEBHOOK_UNAUTHORIZED', 'invalid client state')
    _queue(o, c, ids)
    return Response(status_code=202)
