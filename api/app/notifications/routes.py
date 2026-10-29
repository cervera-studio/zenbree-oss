"""The bell panel (#173), its live stream (#242) and the notification settings page."""

import asyncio
from contextlib import aclosing
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.agent import events
from app.auth.middleware import CurrentUser, current_user
from app.core.config import settings
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.core.event_stream import EventStream
from app.core.locale import DEFAULT_LOCALE
from app.core.scope import system
from app.core.system_reasons import SystemReason
from app.notifications import push
from app.notifications.hrefs import switch_href
from app.notifications.quotes import with_quotes, with_quotes_of_other_organizations
from app.notifications.replay import fresh_frame_data
from app.notifications.service import (
    VISIBLE_SQL,
    browser_on,
    mark_open,
    prefs,
    publish,
    recipient,
    save_prefs,
    stream_cursor,
    stream_key,
    unread_count,
)

router = APIRouter(prefix='/v1/notifications', tags=['Notifications'])

PANEL_LIMIT = 50


class Notification(BaseModel):
    id: UUID
    kind: Literal[
        'reply_urgent',
        'reply',
        'search_done',
        'low_credits',
        'payment_failed',
        'approval_needed',
        'search_failed',
        'agent_question',
        'linkedin_accepted',
        'bounces_high',
        'mailbox_error',
        'plan_renewed',
    ]
    href: str
    data: dict[str, Any]
    read: bool
    created_at: datetime
    browser: bool
    quote: str | None = None  # reply kinds whose message is still there; absent otherwise


class NotificationList(BaseModel):
    items: list[Notification]
    unread: int
    cursor: str | None


class ReadResult(BaseModel):
    updated: int


@router.get('', response_model=NotificationList, response_model_exclude_unset=True)
def list_notifications(user: CurrentUser = Depends(current_user)) -> dict:
    """Newest first, plus the stream ``cursor`` to tail from: read before the rows, so nothing falls between."""
    cursor = stream_cursor(user.id)
    with get_conn() as conn:
        p = prefs(conn, user.id)
        rows = conn.execute(
            f"""SELECT id, organization_id, kind, href, data, read_at IS NOT NULL AS read, created_at
               FROM notifications n WHERE user_id = %s AND {VISIBLE_SQL} ORDER BY created_at DESC LIMIT %s""",
            (user.id, PANEL_LIMIT),
        ).fetchall()
        unread = unread_count(conn, user.id)
        with_quotes(conn, user.id, [r for r in rows if str(r['organization_id']) == user.org_id])
    with_quotes_of_other_organizations(user.id, user.org_id, rows)
    for r in rows:
        r['href'] = switch_href(str(r.pop('organization_id')), r['href'])  # the panel names no organization
        r['browser'] = browser_on(p, r['kind'])
    return {'items': rows, 'unread': unread, 'cursor': cursor}


async def _sse(user_id: str, org_id: str, after: str):
    """Tail the user's stream; while it is open, the user counts as having a Zenbree tab open."""
    async with aclosing(events.read_stream(stream_key(user_id), after)) as entries:
        await asyncio.to_thread(mark_open, user_id)
        async for item in entries:
            await asyncio.to_thread(mark_open, user_id)
            if item is None:
                yield ': ping\n\n'
                continue
            entry_id, fields = item
            data: str | None = fields['data']
            if fields['event'] == 'notification':
                data = await asyncio.to_thread(fresh_frame_data, user_id, org_id, fields['data'])
                if data is None:
                    continue
            yield f'event: {fields["event"]}\nid: {entry_id}\ndata: {data}\n\n'


@router.get('/stream', response_class=EventStream)
async def stream(
    user: CurrentUser = Depends(current_user),
    last_event_id: str | None = Header(default=None),
) -> EventStream:
    """SSE: ``notification`` and ``read`` frames after ``Last-Event-ID`` (else from now), ``: ping`` every 15 s."""
    after = events.entry_id(last_event_id) or await asyncio.to_thread(stream_cursor, user.id)
    if after is None:
        raise api_error(503, 'NOTIFICATIONS.STREAM_UNAVAILABLE', 'stream unavailable')
    return EventStream(_sse(user.id, user.org_id, after))


class ReadIn(BaseModel):
    ids: list[UUID] | None = None  # None: all of them


@router.post('/read', response_model=ReadResult)
def mark_read(body: ReadIn, user: CurrentUser = Depends(current_user)) -> dict:
    with get_conn() as conn:
        n = conn.execute(
            """UPDATE notifications SET read_at = now()
               WHERE user_id = %s AND read_at IS NULL AND (%s::uuid[] IS NULL OR id = ANY(%s::uuid[]))""",
            (user.id, body.ids, body.ids),
        ).rowcount
        unread = unread_count(conn, user.id)
    if n:  # after the commit, so other tabs never see a read that rolled back
        publish(user.id, 'read', {'ids': body.ids and [str(i) for i in body.ids], 'unread': unread})
    return {'updated': n}


class Channels(BaseModel):
    email: bool
    browser: bool


class BrowserOnly(BaseModel):
    """A row whose kinds always email: a stale tab may still send ``email``, which is ignored."""

    browser: bool


class Events(BaseModel):
    """The rows of service.ROW_DEFAULTS. A row the body leaves out keeps the saved choice (a tab loaded
    before a row existed); a kind with no row (payment_failed) is a 422."""

    model_config = ConfigDict(extra='forbid')
    reply_urgent: Channels | None = None
    reply: Channels | None = None
    linkedin_accepted: Channels | None = None
    approval_needed: Channels | None = None
    search_done: Channels | None = None
    search_failed: Channels | None = None
    bounces_high: BrowserOnly | None = None
    low_credits: BrowserOnly | None = None


class Digest(BaseModel):
    frequency: Literal['daily', 'weekly', 'off']
    hour: int = Field(ge=0, le=23)


class SettingsIn(BaseModel):
    events: Events
    digest: Digest
    dnd: bool


class EventChannels(BaseModel):
    reply_urgent: Channels
    reply: Channels
    linkedin_accepted: Channels
    approval_needed: Channels
    search_done: Channels
    search_failed: Channels
    bounces_high: BrowserOnly
    low_credits: BrowserOnly


class NotificationSettings(BaseModel):
    events: EventChannels
    digest: Digest
    dnd: bool


def _settings_out(p: dict) -> dict:
    return {'events': p['events'], 'digest': p['digest'], 'dnd': p['dnd']}


@router.get('/settings', response_model=NotificationSettings)
def get_settings(user: CurrentUser = Depends(current_user)) -> dict:
    with get_conn() as conn:
        return _settings_out(prefs(conn, user.id))


@router.put('/settings', response_model=NotificationSettings)
def put_settings(body: SettingsIn, user: CurrentUser = Depends(current_user)) -> dict:
    with get_conn() as conn:
        saved = prefs(conn, user.id)['events']
        sent = body.events.model_dump(exclude_none=True)
        events = {k: {**v, **sent.get(k, {})} for k, v in saved.items()}
        save_prefs(conn, user.id, events, body.digest.model_dump(), body.dnd)
        return _settings_out(prefs(conn, user.id))


class Subscription(BaseModel):
    """What ``PushSubscription.toJSON()`` gives, flattened."""

    endpoint: str = Field(max_length=2048)
    p256dh: str = Field(min_length=1, max_length=256)
    auth: str = Field(min_length=1, max_length=64)


class Endpoint(BaseModel):
    endpoint: str = Field(max_length=2048)


class PushConfig(BaseModel):
    public_key: str | None
    subscribed: list[str]


class PushSaved(BaseModel):
    ok: bool


class PushDeleted(BaseModel):
    deleted: int


class PushSent(BaseModel):
    sent: bool


def _push_unavailable() -> HTTPException:
    return api_error(503, 'NOTIFICATIONS.PUSH_UNAVAILABLE', 'push is not configured')


@router.get('/push', response_model=PushConfig)
def get_push(user: CurrentUser = Depends(current_user)) -> dict:
    """The key a browser subscribes with (``None``: push is inert) and this user's subscribed endpoints, so a
    browser can tell its own subscription from one another account holds."""
    with get_conn() as conn:
        subscribed = push.endpoints(conn, user.id)
    return {'public_key': settings.vapid_public_key if push.enabled() else None, 'subscribed': subscribed}


@router.put('/push', response_model=PushSaved)
def put_push(body: Subscription, user: CurrentUser = Depends(current_user)) -> dict:
    if not push.enabled():
        raise _push_unavailable()
    if not push.valid_endpoint(body.endpoint):
        raise api_error(422, 'NOTIFICATIONS.PUSH_ENDPOINT', 'not a browser push service')
    # The endpoint's row may be another user's, in an organization this one cannot see: its auth secret decides.
    with system(SystemReason.PUSH_TAKEOVER), get_conn() as conn:
        if not push.subscribe(conn, user.id, body.endpoint, body.p256dh, body.auth):
            raise api_error(409, 'NOTIFICATIONS.PUSH_TAKEN', 'another account holds this subscription')
    return {'ok': True}


@router.delete('/push', response_model=PushDeleted)
def delete_push(body: Endpoint, user: CurrentUser = Depends(current_user)) -> dict:
    with get_conn() as conn:
        return {'deleted': push.unsubscribe(conn, user.id, body.endpoint)}


@router.post('/push/test', response_model=PushSent)
def send_test_push(body: Endpoint, user: CurrentUser = Depends(current_user)) -> dict:
    """A test alert to this browser only, so the user sees the permission work."""
    if not push.enabled():
        raise _push_unavailable()
    with get_conn() as conn:
        _, locale = recipient(conn, user.id) or (None, DEFAULT_LOCALE)
    statuses = push.send(user.id, push.check_payload(locale), only=body.endpoint)
    if not statuses:
        raise api_error(409, 'NOTIFICATIONS.PUSH_NO_SUBSCRIPTION', 'this browser is not subscribed')
    if statuses[0] in push.GONE:
        raise api_error(409, 'NOTIFICATIONS.PUSH_NO_SUBSCRIPTION', 'this subscription expired')
    if statuses[0] is None or statuses[0] >= 300:
        raise api_error(502, 'NOTIFICATIONS.PUSH_FAILED', 'the push service refused it')
    return {'sent': True}
