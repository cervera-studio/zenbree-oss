"""Inbound email webhook (Resend) and the reply inbox API (list, relabel). The unsubscribe link is in
app/outreach/unsubscribe_routes.py."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from email.utils import parseaddr
from typing import Literal, get_args
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.auth.middleware import CurrentUser, SystemReason, current_user, public
from app.core.config import settings
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.core.rate_limit import hit
from app.core.reply_address import ReplyKey, normalise_address, parse_reply_address
from app.llm.base import ProviderAPIError, ProviderRateLimitError
from app.outreach import inbox_reply, replies, sender_holds
from app.outreach.inbound import message_ids, verify_svix
from app.projects.routes import current_project

router = APIRouter(tags=['Inbox'])
logger = structlog.get_logger()

_RESEND = 'https://api.resend.com'


def _key(data: dict) -> ReplyKey | None:
    """What our signed reply address names, among every recipient (a reply-all reorders them)."""
    for field in ('received_for', 'to', 'cc', 'bcc'):
        raw = data.get(field)
        for item in raw if isinstance(raw, list) else [raw]:
            key = parse_reply_address(str(item or ''), secret=settings.outreach_reply_secret)
            if key:
                return key
    return None


async def _content(email_id: str) -> tuple[str, dict[str, str], object] | None:
    """Resend's webhook is metadata-only: text, headers and the receiver's own ``authentication`` verdicts come
    from its receiving API."""
    if not settings.resend_api_key or not email_id or '/' in email_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f'{_RESEND}/emails/receiving/{email_id}',
                headers={'Authorization': f'Bearer {settings.resend_api_key}'},
            )
        payload = resp.json() if resp.status_code < 300 else None
    except httpx.HTTPError, ValueError:
        payload = None
    if not isinstance(payload, dict):
        return None
    raw_headers = payload.get('headers')
    pairs = (
        list(raw_headers.items())
        if isinstance(raw_headers, dict)
        else [(h.get('name'), h.get('value')) for h in raw_headers or [] if isinstance(h, dict) and h.get('name')]
    )
    headers: dict[str, str] = {}
    for name, value in pairs:
        first = value[0] if isinstance(value, list) and value else value
        headers.setdefault(str(name).lower(), str(first or ''))
    # ponytail: an HTML-only reply is stored empty-bodied (labelled from the subject); add an HTML→text pass if seen.
    return str(payload.get('text') or ''), headers, payload.get('authentication')


@router.post('/v1/inbound/email', include_in_schema=False, dependencies=[public(SystemReason.INBOUND_MAIL)])
async def inbound_email(request: Request) -> dict:
    """Resend ``email.received`` for mail sent to a signed reply address (``app.core.reply_address``).

    Unauthenticated by nature: the Svix signature is the only thing between the internet and a
    forged reply that stops someone's campaign. 200 for anything that is not ours, so Resend keeps
    the endpoint enabled; 503 when content cannot be read, so it retries.
    """
    secret = settings.outreach_inbound_webhook_secret
    if not (secret and settings.outreach_reply_secret and settings.resend_api_key):
        raise api_error(503, 'INBOUND.NOT_CONFIGURED', 'inbound mail is not configured')
    body = await request.body()  # capped by app.core.body_limit before anything reads it
    h = request.headers
    if not verify_svix(
        secret=secret,
        msg_id=h.get('svix-id', ''),
        timestamp=h.get('svix-timestamp', ''),
        signature=h.get('svix-signature', ''),
        body=body,
    ):
        logger.warning('inbound.bad_signature')
        raise api_error(401, 'INBOUND.UNAUTHORIZED', 'signature verification failed')
    try:
        payload = json.loads(body)
    except ValueError:
        payload = None
    data = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(data, dict) or payload.get('type') != 'email.received':
        return {'status': 'ignored'}
    key = _key(data)
    name, address = parseaddr(str(data.get('from') or ''))
    sender = normalise_address(address)
    if key is None or not sender:
        return {'status': 'ignored'}
    # Always read: thread ids, Auto-Submitted and the DMARC verdict live only in the full message.
    content = await _content(str(data.get('email_id') or ''))
    if content is None:
        raise api_error(503, 'INBOUND.CONTENT_UNAVAILABLE', 'could not read the message, retry')
    text, headers, authentication = content
    refs = message_ids(headers.get('in-reply-to', ''), headers.get('references', ''))
    return await asyncio.to_thread(
        replies.record,
        None if key.campaign else key.id,  # ponytail: the old owner form, see reply_targets.in_organization
        campaign_id=key.id if key.campaign else None,
        channel='email',
        provider_message_id=str(data.get('email_id') or data.get('message_id') or h.get('svix-id')),
        sender=sender,
        sender_name=name,
        subject=str(data.get('subject') or ''),
        text=text,
        headers=headers,
        authentication=authentication if isinstance(authentication, dict) else None,
        references=refs,
        rfc_message_id=headers.get('message-id', ''),
    )


# ── inbox API ────────────────────────────────────────────────────────────────

Label = Literal['interested', 'meeting_request', 'not_interested', 'not_now', 'other']
_COUNTS = (
    'count(*) AS "all", count(*) FILTER (WHERE read_at IS NULL) AS unread,'
    " count(*) FILTER (WHERE kind = 'unsubscribe') AS unsubscribe,"
    ' count(*) FILTER (WHERE label IS NULL) AS "none",'
    + ','.join(f" count(*) FILTER (WHERE label = '{label}') AS {label}" for label in get_args(Label))
)


class InboxRow(BaseModel):
    id: UUID
    channel: Literal['email', 'linkedin']
    kind: Literal['reply', 'auto_reply', 'unsubscribe']
    from_address: str
    from_name: str
    subject: str
    body: str
    campaign_id: UUID | None
    target_id: UUID | None
    label: Label | None
    label_source: Literal['llm', 'manual'] | None
    received_at: datetime
    read_at: datetime | None
    sender_authenticated: bool | None


_COLUMNS = ', '.join(InboxRow.model_fields)


class InboxMessage(InboxRow):
    campaign_name: str | None
    target_first_name: str | None
    target_last_name: str | None
    target_company_name: str | None
    target_role: str | None
    target_status: Literal['active', 'draft', 'paused', 'excluded', 'replied'] | None
    target_paused_until: datetime | None


class InboxCounts(BaseModel):
    all: int
    unread: int
    unsubscribe: int
    none: int
    interested: int
    meeting_request: int
    not_interested: int
    not_now: int
    other: int


class Inbox(BaseModel):
    messages: list[InboxMessage]
    counts: InboxCounts


class SentStep(BaseModel):
    step: int
    channel: Literal['email', 'linkedin_connect', 'linkedin_message', 'hubspot_task']
    subject: str
    body: str | None
    delivered_at: datetime


class InboxAnswer(BaseModel):
    id: UUID
    channel: Literal['email', 'linkedin']
    subject: str
    body: str
    status: Literal['sending', 'sent', 'failed', 'unknown']
    error_code: str | None
    created_at: datetime
    delivered_at: datetime | None


class Reminder(BaseModel):
    remind_on: date
    surfaced_at: datetime | None


class Thread(BaseModel):
    message: InboxMessage
    reminder: Reminder | None
    sent: list[SentStep]
    replies: list[InboxAnswer]
    draft: str | None
    reply_from: str | None
    reply_blocked: (
        Literal[
            'INBOX.UNSUBSCRIBED',
            'INBOX.EXCLUDED',
            'INBOX.NO_RECIPIENT',
            'INBOX.NO_MAILBOX',
            'INBOX.NO_LINKEDIN',
            'INBOX.ACCOUNT_PAUSED',
        ]
        | None
    )
    reply_hold: sender_holds.ReplyHold | None


@router.get('/v1/inbox', response_model=Inbox)
def list_inbox(
    limit: int = 50,
    offset: int = 0,
    label: Label | Literal['none'] | None = None,
    channel: Literal['email', 'linkedin'] | None = None,
    kind: Literal['reply', 'auto_reply', 'unsubscribe'] | None = None,
    unread: bool = False,
    campaign_id: UUID | None = None,
    sort: Literal['newest', 'oldest'] = 'newest',
    project_id: str = Depends(current_project),
    user: CurrentUser = Depends(current_user),
) -> dict:
    """The active project's replies (#307), newest first. ``label=none``: not labelled yet. Campaign and person
    come with each row (#87). ``counts``: per filter, within ``channel`` (#187) and ``campaign_id``. With a
    ``campaign_id`` the campaign's own project is read, whichever client is active (the campaign page is
    organization-wide); 404 for another organization's."""
    limit, offset = max(1, min(limit, 200)), max(0, offset)
    with get_conn() as conn:
        if campaign_id:
            found = conn.execute(
                'SELECT c.project_id FROM outreach_campaigns c JOIN projects p ON p.id = c.project_id'
                ' WHERE c.id = %s AND p.organization_id = %s',
                (campaign_id, user.org_id),
            ).fetchone()
            if found is None:
                raise api_error(404, 'CAMPAIGN.NOT_FOUND', 'campaign not found')
            project_id = str(found['project_id'])
        rows = conn.execute(
            f'SELECT {", ".join("m." + c.strip() for c in _COLUMNS.split(","))},'
            ' c.name AS campaign_name, t.first_name AS target_first_name, t.last_name AS target_last_name,'
            ' t.company_name AS target_company_name, t.role AS target_role,'
            ' t.status AS target_status, t.paused_until AS target_paused_until'
            ' FROM outreach_inbound_messages m'
            ' LEFT JOIN outreach_campaigns c ON c.id = m.campaign_id AND c.owner_id IS NOT DISTINCT FROM m.owner_id'
            ' LEFT JOIN outreach_campaign_targets t ON t.id = m.target_id AND t.owner_id IS NOT DISTINCT FROM m.owner_id'
            ' WHERE m.project_id = %(project)s'
            "   AND (%(label)s::text IS NULL OR m.label IS NOT DISTINCT FROM nullif(%(label)s, 'none'))"
            '   AND (%(channel)s::text IS NULL OR m.channel = %(channel)s)'
            '   AND (%(kind)s::text IS NULL OR m.kind = %(kind)s)'
            '   AND (NOT %(unread)s OR m.read_at IS NULL)'
            '   AND (%(campaign)s::uuid IS NULL OR m.campaign_id = %(campaign)s)'
            f' ORDER BY m.received_at {"ASC" if sort == "oldest" else "DESC"} LIMIT %(limit)s OFFSET %(offset)s',
            {
                'project': project_id,
                'label': label,
                'channel': channel,
                'kind': kind,
                'unread': unread,
                'campaign': campaign_id,
                'limit': limit,
                'offset': offset,
            },
        ).fetchall()
        counts = conn.execute(
            f'SELECT {_COUNTS} FROM outreach_inbound_messages WHERE project_id = %s'
            ' AND (%s::text IS NULL OR channel = %s) AND (%s::uuid IS NULL OR campaign_id = %s)',
            (project_id, channel, channel, campaign_id, campaign_id),
        ).fetchone()
    return {'messages': rows, 'counts': counts}


def _message_project(message_id: UUID, user: CurrentUser) -> str:
    """The project of one of the organization's messages, whichever client is active: a notification or a
    campaign links a reply by id (#307). 404 for another organization's."""
    with get_conn() as conn:
        row = conn.execute(
            'SELECT m.project_id FROM outreach_inbound_messages m JOIN projects p ON p.id = m.project_id'
            ' WHERE m.id = %s AND p.organization_id = %s',
            (message_id, user.org_id),
        ).fetchone()
    if row is None:
        raise api_error(404, 'INBOX.NOT_FOUND', 'message not found')
    return str(row['project_id'])


class LabelIn(BaseModel):
    label: Label = Field()


@router.patch('/v1/inbox/{message_id}', response_model=InboxRow)
def relabel(message_id: UUID, body: LabelIn, user: CurrentUser = Depends(current_user)) -> dict:
    """A person's label wins: the label task never overwrites a manual one. «Non ora» keeps a reminder (#346)."""
    project_id = _message_project(message_id, user)
    with get_conn() as conn:
        row = conn.execute(
            f"UPDATE outreach_inbound_messages SET label = %s, label_source = 'manual'"
            f' WHERE id = %s AND project_id = %s RETURNING {_COLUMNS}',
            (body.label, message_id, project_id),
        ).fetchone()
        if row is not None:
            replies.sync_reminder(conn, str(message_id), body.label)
    if row is None:
        raise api_error(404, 'INBOX.NOT_FOUND', 'message not found')
    return row


@router.get('/v1/inbox/{message_id}', response_model=Thread)
def get_thread(message_id: UUID, mark_read: bool = True, user: CurrentUser = Depends(current_user)) -> dict:
    """One reply as a thread (#187). Opening it marks it read unless ``mark_read=false``."""
    project_id = _message_project(message_id, user)
    found = inbox_reply.thread(project_id, str(message_id), mark_read=mark_read)
    if found is None:
        raise api_error(404, 'INBOX.NOT_FOUND', 'message not found')
    if found['reply_blocked'] == 'INBOX.NO_MAILBOX' and (target := found['message']['target_id']):
        with get_conn() as conn:
            found['reply_hold'] = sender_holds.reply_hold(conn, project_id, target, datetime.now(UTC))
    return {'reply_hold': None, **found}


class ReplyIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


@router.post('/v1/inbox/{message_id}/reply', response_model=InboxAnswer)
def send_reply(message_id: UUID, body: ReplyIn, user: CurrentUser = Depends(current_user)) -> dict:
    """Send a member's answer through the channel the reply came in on (#187), from the project's sender. Sync
    on purpose: the send reads and writes the database between provider calls, off the event loop in the thread
    pool."""
    try:
        return asyncio.run(inbox_reply.send(_message_project(message_id, user), str(message_id), body.body))
    except inbox_reply.ReplyError as exc:
        raise api_error(exc.status, exc.code, exc.message) from None


class DraftOut(BaseModel):
    draft: str


# Real calls take 1 to 3 s; the provider chain alone could hold the button for minutes. A timeout cancels the
# call before the draft is written, so the error the user reads matches the stored draft.
DRAFT_TIMEOUT_S = 30.0


def _read_draft(message_id: UUID, project_id: str) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            'SELECT draft_body FROM outreach_inbound_messages WHERE id = %s AND project_id = %s',
            (message_id, project_id),
        ).fetchone()


@router.post('/v1/inbox/{message_id}/draft', response_model=DraftOut)
async def regenerate_draft(message_id: UUID, user: CurrentUser = Depends(current_user)) -> dict:
    """Ask the agent for a new draft answer, overwriting the current one. The model call is the point of the
    request; it awaits on the event loop, so a slow provider holds no threadpool slot. 429 `INBOX.RATE_LIMITED`
    past 10 a minute per user. 409 when the reply gets no draft (a decline, an unverified sender, unlabelled, an
    auto-reply or opt-out) or the model gave none, the old draft staying; 502 when the model provider fails or
    runs past the timeout."""
    # ponytail: 10 regenerations a minute per user; no credit charge (a pricing call), a daily quota if it bites.
    await asyncio.to_thread(
        hit, user.id, 'inbox_draft', 10, 60, 'INBOX.RATE_LIMITED', 'Too many draft requests; try again later'
    )
    project_id = await asyncio.to_thread(_message_project, message_id, user)
    try:
        written = await asyncio.wait_for(inbox_reply.draft(str(message_id), regenerate_by=user.id), DRAFT_TIMEOUT_S)
    except (ProviderAPIError, ProviderRateLimitError, TimeoutError) as e:
        raise api_error(502, 'INBOX.LLM_FAILED', 'could not write a draft, try again') from e
    if not written:
        raise api_error(409, 'INBOX.NO_DRAFT', 'no draft could be written for this reply')
    row = await asyncio.to_thread(_read_draft, message_id, project_id)
    if row is None:  # deleted while the model wrote
        raise api_error(404, 'INBOX.NOT_FOUND', 'message not found')
    return {'draft': row['draft_body']}
