"""Beat-driven campaign sending: the send scheduler (#80), the email step (#81) and the LinkedIn step (#82).

Adapted from the earlier implementation ``scheduling/campaign_send_dispatcher.py``, ``outreach/use_cases/send_campaign_step.py``,
``send_campaign_linkedin.py`` and ``integrations/use_cases/send_from_mailbox.py``. Each beat runs two phases:

1. ``plan``: for every ``active`` target of an ``active`` campaign whose next step is due, write a
   durable claim in ``outreach_campaign_sends`` with the copy rendered for that person.
   ``approve_before_sending`` (default on) makes it ``awaiting_approval``: nothing below sends it
   until a person approves the row. Excluded people are marked ``excluded`` and never claimed.
2. ``send_due``: at most one ``approved`` email per connected, unpaused mailbox per beat, inside the
   campaign window (mailbox time zone), under the mailbox's warm-up cap and after its random
   gap. The row moves to ``sending`` in the same transaction that takes the mailbox's pacing
   slot, BEFORE the provider call. An outcome we cannot know (timeout after the request left,
   5xx, worker killed) stays ``sending`` and is never retried automatically.

Every phase runs inside one organization's scope (``app.outreach.campaign_beat``), and a send goes out only
through a sender of the campaign's own project (#307). ``channel_disabled:email`` stops phase 2;
``vendor_disabled:google|microsoft`` stops that provider.

LinkedIn steps share phase 1 and the claim/approval/unknown-outcome rules; their phase 2
(``send_linkedin_due``) sends at most one action per Unipile account per beat, inside the campaign
window AND 9-18 Mon-Fri Rome, under 20 invites/day, 100/week and 30 DMs/day, after the random gap.
A DM waits for the invitation's acceptance (the Unipile ``new_relation`` webhook dates it); after
three days without one the DM is skipped and the email steps behind it carry on.
``channel_disabled:linkedin`` / ``vendor_disabled:unipile`` stop it.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta

import structlog

from app.admin.settings import is_disabled
from app.billing.paid_call import current_user_id, record_vendor_call
from app.billing.vendors import UNIPILE
from app.core.config import settings
from app.core.db import get_conn
from app.core.errors import is_err
from app.core.errors_http import api_error
from app.core.mail_threading import ThreadRef, new_message_id, reply_subject
from app.core.reply_address import reply_key
from app.exclusions.rules import Rules, is_excluded, load_rules
from app.integrations import mailbox_sender, mailbox_watch, token_crypto, webhooks
from app.integrations.oauth_client import OAuthClient
from app.integrations.providers import MAILBOX_PROVIDERS, OAuthProvider
from app.linkedin.unipile_client import PROVIDER_LIMIT, UnipileClient
from app.notifications import service as notifications
from app.outreach import allocation, crm_tasks, freeze, send_queue, send_states, sending, variants
from app.outreach import unsubscribe as unsubscribe_token
from app.outreach.claims import advance as _advance
from app.outreach.claims import claim as _claim
from app.outreach.due import (
    GATE_CANDIDATES,
    GATE_PLAN,
    GATE_SEND,
    PLAN_SELECT,
    SENDABLE_CHANNELS,
    exclude_opted_out,
)
from app.outreach.due import due as due_now
from app.outreach.finished import FINISHED
from app.outreach.send_queue import NOT_HELD as _NOT_HELD
from app.outreach.send_queue import OWNER_NOT_BANNED as _OWNER_NOT_BANNED
from app.outreach.sender_slot import take as _take_slot

logger = structlog.get_logger()

_SENT = 'delivered_at = %s, provider_receipt_id = %s, thread_id = %s'
_GIVE_BACK = 'attempts = attempts - 1, error_code = %s'
_DONE = 'error_code = %s, provider_receipt_id = %s, delivered_at = %s'

LINKEDIN_CHANNELS = ('linkedin_connect', 'linkedin_message')
# Head of each mailbox's queue looked at per beat (it sends one): enough to skip rows outside their window.
QUEUE_PER_MAILBOX = 50


# ── phase 1: claims ──────────────────────────────────────────────────────────


def _plan_linkedin(conn, t: dict, n: int, step: dict, now: datetime) -> str | None:
    channel = step['channel']
    url = (t['linkedin_url'] or '').strip()
    if not sending.profile_slug(url):
        shown = (url or (t['email'] or '').strip() or '-')[:500]
        if _claim(conn, t, n, 'skipped', recipient=shown, channel=channel, code='NO_LINKEDIN'):
            _advance(conn, t['id'], n)
        return 'skipped_no_linkedin'
    # No sent invitation (e.g. it was skipped): the DM is claimed, delivery reads the profile's degree.
    if channel == 'linkedin_message' and t['linkedin_accepted_at'] is None and t['invited_at']:
        if now < t['invited_at'] + sending.LINKEDIN_ACCEPT_WAIT:
            return None
        # No acceptance three days after the invitation: skip, so the email steps go on.
        if _claim(conn, t, n, 'skipped', recipient=url, channel=channel, code='NOT_ACCEPTED'):
            _advance(conn, t['id'], n)
        return 'skipped_not_accepted'
    body = sending.render_values(step['body'], freeze.frozen_values(t))
    if is_err(body):
        _claim(conn, t, n, 'refused', recipient=url, channel=channel, code=body.error.code)
        return 'refused_copy'
    limit = sending.NOTE_MAX_PREMIUM if channel == 'linkedin_connect' else sending.MAX_BODY
    if len(body.value) > limit:
        _claim(conn, t, n, 'refused', recipient=url, channel=channel, code='COPY_TOO_LONG')
        return 'refused_copy'
    status = 'awaiting_approval' if t['approve_before_sending'] else 'approved'
    return status if _claim(conn, t, n, status, recipient=url, channel=channel, body=body.value) else None


def _plan_one(conn, t: dict, rules: Rules, now: datetime) -> str | None:
    steps, n = variants.sequence(t['steps'], t['variant']), t['next_step']
    step = steps[n - 1]
    if step['channel'] not in SENDABLE_CHANNELS:
        return 'held_channel'
    if n == 1:
        anchor = t['activated_at']
    elif t['prev_status'] in ('sent', 'skipped'):
        anchor = t['prev_delivered_at'] or t['prev_created_at']
    else:
        anchor = None
    if anchor and step['channel'] == 'linkedin_message' and t['linkedin_accepted_at']:
        anchor = max(anchor, t['linkedin_accepted_at'])  # a DM's delay counts from the acceptance
    if anchor is None or sending.step_due_at(steps, n, anchor) > now:
        return None
    recipient = sending.single_address(t['email'])
    if is_excluded(rules, {**t, 'email': recipient or ''}):
        # Guarded: a reply or an out-of-office may have changed the person since the plan read them.
        stopped = conn.execute(
            "UPDATE outreach_campaign_targets SET status = 'excluded', paused_until = NULL"
            " WHERE id = %s AND status IN ('active', 'paused') RETURNING id",
            (t['id'],),
        ).fetchone()
        return 'excluded' if stopped else None
    if step['channel'] in LINKEDIN_CHANNELS:
        return _plan_linkedin(conn, t, n, step, now)
    if step['channel'] == crm_tasks.CHANNEL:
        return crm_tasks.plan(conn, t, n, step)
    if recipient is None:
        # No address, or not exactly one valid address: never sent, the sequence moves on.
        code = 'INVALID_EMAIL' if (t['email'] or '').strip() else 'NO_EMAIL'
        shown = ((t['email'] or '').strip() or t['linkedin_url'] or '-')[:500]
        if _claim(conn, t, n, 'skipped', recipient=shown, code=code):
            _advance(conn, t['id'], n)
        return f'skipped_{code.lower()}'
    if step.get('threaded'):
        root = sending.thread_root(steps, n)
        sent = conn.execute(
            'SELECT subject FROM outreach_campaign_sends WHERE campaign_id = %s AND target_id = %s AND step = %s'
            " AND status = 'sent'",
            (t['campaign_id'], t['id'], root),
        ).fetchone()
        if sent is None:
            # "Re:" in front of someone who never got the original reads as a fake history.
            if _claim(conn, t, n, 'skipped', recipient=recipient, code='NO_THREAD'):
                _advance(conn, t['id'], n)
            return 'skipped_no_thread'
        subject = reply_subject(sent['subject'])
    else:
        rendered_subject = sending.render_values(step['subject'], freeze.frozen_values(t))
        if is_err(rendered_subject):
            _claim(conn, t, n, 'refused', recipient=recipient, code=rendered_subject.error.code)
            return 'refused_copy'
        subject = rendered_subject.value
    body = sending.render_values(step['body'], freeze.frozen_values(t))
    if is_err(body):
        _claim(conn, t, n, 'refused', recipient=recipient, code=body.error.code)
        return 'refused_copy'
    if len(subject) > sending.MAX_SUBJECT or len(body.value) > sending.MAX_BODY:
        # Refused with a reason a person can act on; never truncated, never a CHECK failure every beat.
        _claim(conn, t, n, 'refused', recipient=recipient, code='COPY_TOO_LONG')
        return 'refused_copy'
    status = 'awaiting_approval' if t['approve_before_sending'] else 'approved'
    return status if _claim(conn, t, n, status, recipient=recipient, subject=subject, body=body.value) else None


def gate_candidates(now: datetime) -> dict[str, list[str]]:
    """Owner id → the lower-cased emails of the people a step is due for now and no verdict blocks."""
    with get_conn() as conn:
        select = 't.owner_id, lower(btrim(t.email)) AS email'
        rows = due_now(conn, now, select=select, gate_sql=GATE_CANDIDATES)
    people: dict[str, list[str]] = {}
    for r in rows:
        emails = people.setdefault(str(r['owner_id']), [])
        if r['email'] not in emails:
            emails.append(r['email'])
    return people


def plan(now: datetime) -> Counter:
    counts: Counter = Counter()
    with get_conn() as conn:
        if opted_out := exclude_opted_out(conn):
            counts['excluded_crm'] = opted_out
        due = due_now(conn, now, select=PLAN_SELECT, gate_sql=GATE_PLAN)
    rules: dict[str, Rules] = {}
    held: set[tuple[str, str]] = set()  # (owner, campaign) that held new messages for approval
    planned: set[str] = set()
    for t in due:
        try:
            with get_conn() as conn:  # one transaction per person: a poison row stops nobody else
                owner, project = str(t['owner_id']), str(t['project_id'])
                if project not in rules:
                    rules[project] = load_rules(conn, project)
                if outcome := _plan_one(conn, t, rules[project], now):
                    counts[outcome] += 1
                if outcome == 'awaiting_approval' and t['owner_id']:  # #333: no creator left to tell
                    held.add((owner, str(t['campaign_id'])))
                if outcome in ('approved', 'awaiting_approval', 'skipped_invalid_email'):
                    planned.add(str(t['campaign_id']))
            if outcome == 'excluded':
                webhooks.sequence_stopped([t], 'excluded')
        except Exception as exc:
            logger.error('campaign.plan.target_failed', target_id=str(t['id']), error_type=type(exc).__name__)
            counts['errors'] += 1
    for owner, campaign_id in held:  # #215: after the claims commit
        try:
            with get_conn() as conn:
                notified = notifications.notify_approvals(conn, owner, campaign_id)
            notifications.deliver(notified)
        except Exception as exc:
            logger.error('campaign.plan.notify_failed', campaign_id=campaign_id, error_type=type(exc).__name__)
    for campaign_id in planned:
        _check_bounces(campaign_id)
    return counts


# ── phase 2: email delivery ──────────────────────────────────────────────────


def _settle(s: dict, status: str, code: str | None, *, claimed: bool, target_status: str | None = None) -> None:
    """Before the claim, only while the send is still claimable: another beat may hold it in flight by now."""
    with get_conn() as conn:  # lock order: target, then send (see sender_slot)
        stopped = (
            conn.execute(
                # a reply that landed during the send already stopped the person: it stays the reason
                'UPDATE outreach_campaign_targets SET status = %s, paused_until = NULL'
                " WHERE id = %s AND status IN ('active', 'draft', 'paused')"
                ' RETURNING id, campaign_id, email, linkedin_url',
                (target_status, s['target_id']),
            ).fetchall()
            if target_status
            else []
        )
        frm = ('sending',) if claimed else send_states.sources('sending')
        send_states.move(conn, s['id'], frm, status, set_='error_code = %s', params=(code,))
    webhooks.sequence_stopped(stopped, target_status or '')


def _mark_mailbox_error(mb: dict, detail: str) -> None:
    with get_conn() as conn:
        failed = conn.execute(
            "UPDATE user_oauth_connections SET status = 'error', status_detail = %s WHERE id = %s AND status = 'connected'"
            f' RETURNING {", ".join(notifications.CONNECTION_FIELDS)}',
            (detail[:500], mb['id']),
        ).fetchone()  # none: another turn already moved it, and told the user
        notified = notifications.notify_mailbox_error(conn, failed) if failed else None
    notifications.deliver(notified)
    logger.warning('campaign.send.mailbox_error', user_id=str(mb['user_id']), provider=mb['provider'], detail=detail)


def _check_bounces(campaign_id: str) -> None:
    try:
        with get_conn() as conn:
            notified = notifications.notify_bounces(conn, campaign_id)
        notifications.deliver(notified)
    except Exception as exc:  # a failed notice must not stop the beat
        logger.error('campaign.bounces.notify_failed', campaign_id=campaign_id, error_type=type(exc).__name__)


def _thread(s: dict) -> tuple[bool, ThreadRef | None]:
    """``(sendable, thread)``: a follow-up whose root never went out is held, not sent unthreaded."""
    root = sending.thread_root(s['steps'], s['step'])
    if not s['steps'][s['step'] - 1].get('threaded') or root is None:
        return True, None
    with get_conn() as conn:
        chain = conn.execute(
            'SELECT step, message_id, thread_id FROM outreach_campaign_sends WHERE campaign_id = %s AND target_id = %s'
            " AND channel = 'email' AND status = 'sent' AND step >= %s AND step < %s ORDER BY step",
            (s['campaign_id'], s['target_id'], root, s['step']),
        ).fetchall()
    if not chain or chain[0]['step'] != root:
        return False, None
    ids = tuple(r['message_id'] for r in chain if r['message_id'])
    return True, ThreadRef(
        message_id=ids[-1] if ids else '', references=ids, provider_thread_id=chain[0]['thread_id'] or ''
    )


async def _access_token(mb: dict, provider: OAuthProvider, *, raise_outage: bool = False) -> str | None:
    """A fresh access token, persisting a rotated refresh token. ``None`` holds this mailbox's turn: the grant
    is gone or the provider failed. Only a revoked grant marks the mailbox. An outage (5xx, timeout, 429) or the
    provider rejecting our own OAuth client leaves it connected, and raises 503 for a request (``raise_outage``)."""
    refresh_token = token_crypto.decrypt(mb['refresh_token_encrypted'] or '')
    if is_err(refresh_token):
        _mark_mailbox_error(mb, refresh_token.error.code)
        return None
    grant = await OAuthClient(provider).refresh(refresh_token.value)
    if is_err(grant):
        if grant.error.code == 'INTEGRATIONS.AUTH':  # revoked: the settings page offers Reconnect
            _mark_mailbox_error(mb, grant.error.code)
        elif raise_outage:
            raise api_error(503, 'MAILBOX.TEMPORARILY_UNAVAILABLE', 'the mailbox provider is not answering')
        return None
    rotated = grant.value.refresh_token
    if rotated and rotated != refresh_token.value:
        # Microsoft rotates on every exchange: losing the new one kills the connection next send.
        encrypted = token_crypto.encrypt(rotated)
        if not is_err(encrypted):
            with get_conn() as conn:
                stored = conn.execute(
                    'UPDATE user_oauth_connections SET refresh_token_encrypted = %s WHERE id = %s',
                    (encrypted.value, mb['id']),
                ).rowcount
            # Row-level security refuses a write to a grant outside the scope with no error: a HubSpot grant written
            # as anyone but its owner. Logged, not raised: this access token still works, the next refresh fails.
            if not stored:
                logger.error('oauth.rotated_token_not_stored', connection_id=str(mb['id']), provider=mb['provider'])
    return grant.value.access_token


async def _deliver(s: dict, mb: dict, rules: Rules, now: datetime) -> str:
    provider = MAILBOX_PROVIDERS[mb['provider']]
    recipient = sending.single_address(s['recipient'])
    if recipient is None:
        _settle(s, 'refused', 'INVALID_EMAIL', claimed=False)
        _check_bounces(str(s['campaign_id']))
        return 'refused_recipient'
    # Checked on the address we are about to write to, not only the one on the target row.
    if is_excluded(rules, {**s, 'email': recipient}):
        _settle(s, 'skipped', 'EXCLUDED', claimed=False, target_status='excluded')
        return 'excluded'
    # #172/#220: the footer links the page; the header's one-click POST goes to unsubscribe.one_click_url.
    key = reply_key(s['owner_id'], s['project_id'])  # the client's once the creator is gone (#333)
    token = unsubscribe_token.mint(key, recipient, secret=settings.outreach_reply_secret)
    one_click = unsubscribe_token.one_click_url(token) if token else None
    body = sending.compose_body(
        s['body'] or '', mb['sign_off'] or '', unsubscribe_token.page_url(token) if token else None
    )
    if not s['body'] or sending.find_placeholder(s['subject'], body):
        _settle(s, 'refused', 'PLACEHOLDER_IN_MESSAGE' if s['body'] else 'NO_BODY', claimed=False)
        return 'refused_copy'
    sendable, thread = _thread(s)
    if not sendable:
        _settle(s, 'refused', 'NO_THREAD', claimed=False)
        return 'refused_thread'
    # ponytail: no Reply-To until OUTREACH_REPLY_DOMAIN/SECRET are set, i.e. until #86 receives replies.
    reply_to = sending.reply_to(s['campaign_id'])
    # On the sending mailbox's own domain (a `.local` id hurts deliverability); kept across retries.
    message_id = s['message_id'] or new_message_id(domain=sending.mail_domain(mb))
    try:  # built BEFORE the claim: a message that cannot be built must never sit in `sending`
        raw = mailbox_sender.build_message(
            sender=mb['account_label'] or '',
            sender_name=mb['display_name'] or '',
            to=recipient,
            subject=s['subject'],
            text=body,
            message_id=message_id,
            reply_to=reply_to,
            thread=thread,
            unsubscribe_url=one_click,
        )
    except ValueError:  # a CR/LF in a header value (subject, display name)
        _settle(s, 'refused', 'INVALID_CONTENT', claimed=False)
        return 'refused_copy'
    if not provider.grants_send(mb['granted_scopes'] or []):
        _mark_mailbox_error(mb, 'send permission no longer granted')
        return 'mailbox_error'
    access_token = await _access_token(mb, provider)
    if access_token is None:
        return 'token_failed'
    await mailbox_watch.keep_watching(mb, access_token, now)  # #747: its bounces are read while it sends
    if is_disabled('channel_disabled:email') or not _take_slot(s, mb, provider.name, now, message_id):
        return 'lost_slot'
    thread_id = thread.provider_thread_id if thread else ''
    sent = await mailbox_sender.send(provider, access_token=access_token, raw=raw, thread_id=thread_id)
    if not is_err(sent):
        receipt = (now, sent.value.message_id or None, sent.value.thread_id or thread_id)
        with get_conn() as conn:
            if send_states.move(conn, s['id'], ('sending',), 'sent', set_=_SENT, params=receipt):
                _advance(conn, s['target_id'], s['step'])
        await mailbox_watch.delivered(mb, access_token, {**s, 'message_id': message_id}, sent.value.message_id)
        return 'sent'
    return _fail(s, mb, sent.error.code, now, ambiguous=bool(sent.error.context.get('delivery_ambiguous')))


def _fail(s: dict, account: dict, code: str, now: datetime, *, ambiguous: bool) -> str:
    """A provider error after the claim (mailbox or Unipile account): unknown, rest, retry or refuse."""
    log = logger.bind(send_id=str(s['id']), user_id=str(s['owner_id']), provider=account['provider'])
    if ambiguous:
        # It may have reached the person: stays `sending`, never resent without a person checking.
        with get_conn() as conn:
            send_states.keep_sending(conn, s['id'], code)
        log.warning('campaign.send.outcome_unknown', error_code=code)
        return 'unknown'
    rest = {'INTEGRATIONS.RATE_LIMITED': sending.RATE_LIMIT_PAUSE, PROVIDER_LIMIT: sending.LINKEDIN_LIMIT_COOLDOWN}
    if code in rest:
        # The provider's quota, not this message: rest the whole account (last_used_at in the future).
        with get_conn() as conn:
            conn.execute(
                'UPDATE user_oauth_connections SET last_used_at = %s WHERE id = %s', (now + rest[code], account['id'])
            )
    if code == 'INTEGRATIONS.RATE_LIMITED':
        # ... and give the attempt back.
        with get_conn() as conn:
            send_states.move(conn, s['id'], ('sending',), 'approved', set_=_GIVE_BACK, params=(code,))
        log.warning('campaign.send.rate_limited')
        return 'rate_limited'
    if code == 'INTEGRATIONS.AUTH':
        _mark_mailbox_error(account, code)
    # A LinkedIn limit spends the attempt: one person LinkedIn keeps refusing is retired in ~3 days.
    retry = code in ('INTEGRATIONS.AUTH', 'INTEGRATIONS.TRANSPORT', PROVIDER_LIMIT)
    final = 'failed' if retry and s['attempts'] + 1 < sending.MAX_ATTEMPTS else 'refused'
    _settle(s, final, code, claimed=True)
    log.warning('campaign.send.failed', error_code=code, status=final)
    return final


async def send_due(now: datetime) -> Counter:
    """Each connected mailbox gets its own head-of-queue: one project's backlog, or rows waiting on a mailbox in
    error, can never fill a shared batch and starve everybody else. Each ready mailbox sends at most one message;
    ``allocation.assign`` picks it before any token refresh. A mailbox may serve several projects (#307): each send
    is judged by its own campaign's project."""
    counts: Counter = Counter()
    if is_disabled('channel_disabled:email'):
        counts['channel_disabled'] += 1
        return counts
    with get_conn() as conn:
        boxes = send_queue.mailboxes(conn, now, QUEUE_PER_MAILBOX)
        rules = {p: load_rules(conn, p) for p in {str(s['project_id']) for b in boxes for s in b.queue}}
    ready = {b.row['id']: b for b in boxes if b.state.state == 'ready'}
    counts.update(str(b.state.reason) for b in boxes if b.state.reason)
    picked = allocation.assign(
        {m: b.queue for m, b in ready.items()}, {m: b.state.daily_cap - b.state.sent_today for m, b in ready.items()}
    )
    counts['idle'] += len(ready) - len(picked)

    async def turn(mb: dict, s: dict) -> str:
        try:
            return await _deliver(s, mb, rules[str(s['project_id'])], now)
        except Exception as exc:  # one mailbox must not stop the others
            logger.error(
                'campaign.send.mailbox_failed',
                user_id=str(mb['user_id']),
                provider=mb['provider'],
                error_type=type(exc).__name__,
            )
            return 'errors'

    for outcome in await asyncio.gather(*(turn(ready[m].row, s) for m, s in picked.items())):
        counts[outcome] += 1
    return counts


# ── phase 2: LinkedIn delivery ───────────────────────────────────────────────


def _linkedin_done(
    s: dict, status: str, code: str | None, *, provider_id: str = '', receipt: str = '', at=None, accepted_at=None
):
    """A LinkedIn row settled for good, in one transaction: the person moves to the next step."""
    with get_conn() as conn:
        done = (code, receipt or None, at if status == 'sent' else None)
        moved = send_states.move(conn, s['id'], ('sending',), status, set_=_DONE, params=done)
        if provider_id:
            conn.execute(
                'UPDATE outreach_campaign_targets SET linkedin_provider_id = %s WHERE id = %s',
                (provider_id, s['target_id']),
            )
        if accepted_at:
            conn.execute(
                'UPDATE outreach_campaign_targets SET linkedin_accepted_at = coalesce(linkedin_accepted_at, %s)'
                ' WHERE id = %s',
                (accepted_at, s['target_id']),
            )
        if moved:
            _advance(conn, s['target_id'], s['step'])


async def _deliver_linkedin(s: dict, acct: dict, rules: Rules, now: datetime) -> str:
    email = sending.single_address(s['email']) or ''
    if is_excluded(rules, {**s, 'email': email, 'linkedin_url': s['recipient']}):
        _settle(s, 'skipped', 'EXCLUDED', claimed=False, target_status='excluded')
        return 'excluded'
    slug = sending.profile_slug(s['recipient'])
    text = s['body'] or ''
    if not slug or sending.find_placeholder(text) or (s['channel'] == 'linkedin_message' and not text.strip()):
        _settle(s, 'refused', 'NO_PROFILE' if not slug else 'PLACEHOLDER_IN_MESSAGE', claimed=False)
        return 'refused_copy'
    if is_disabled('channel_disabled:linkedin') or not _take_slot(s, acct, 'unipile', now):
        return 'lost_slot'
    # Claimed: from here every outcome settles the row. Read the profile before writing anything,
    # because an invitation cannot be taken back.
    current_user_id.set(s['owner_id'] and str(s['owner_id']))
    client, account_id = UnipileClient(), acct['provider_account_id']
    profile = await client.resolve_profile(account_id=account_id, identifier=slug)
    if is_err(profile):
        return _fail(s, acct, profile.error.code, now, ambiguous=False)  # a read: nothing reached the person
    provider_id = str(profile.value.get('provider_id') or '')
    if profile.value.get('is_self') is True or not provider_id:
        _settle(s, 'refused', 'SELF_PROFILE' if provider_id else 'PROFILE_UNRESOLVED', claimed=True)
        return 'refused_profile'
    connected = sending.first_degree(profile.value)
    if s['channel'] == 'linkedin_connect':
        if connected:
            # Already a contact: nothing to ask, and the DMs behind it may go.
            _linkedin_done(s, 'skipped', 'ALREADY_CONNECTED', provider_id=provider_id, accepted_at=now)
            return 'already_connected'
        # ponytail: a note LinkedIn refuses (free plan's monthly allowance) fails the invite; the earlier implementation retries note-less.
        note = sending.invitation_note(text, acct['linkedin_premium'])
        sent = await client.send_invitation(account_id=account_id, provider_id=provider_id, message=note)
        receipt_keys = ('invitation_id', 'id')
    else:
        if not connected:
            # Not first-degree (no invitation sent, or one withdrawn): never DM a stranger.
            _linkedin_done(s, 'skipped', 'NOT_CONNECTED')
            return 'skipped_not_connected'
        sent = await client.send_dm(account_id=account_id, provider_id=provider_id, text=text)
        receipt_keys = ('chat_id', 'id')
    await asyncio.to_thread(record_vendor_call, UNIPILE, s['channel'], metered=1, success=not is_err(sent))
    if is_err(sent):
        # Transport/5xx/unreadable 2xx after the request left: maybe delivered, never resent.
        ambiguous = sent.error.code in ('INTEGRATIONS.TRANSPORT', 'INTEGRATIONS.INVALID_JSON')
        return _fail(s, acct, sent.error.code, now, ambiguous=ambiguous)
    receipt = next((str(sent.value[k]) for k in receipt_keys if sent.value.get(k)), '')
    _linkedin_done(s, 'sent', None, provider_id=provider_id, receipt=receipt[:500], at=now)
    logger.info('campaign.send.delivered', send_id=str(s['id']), provider='unipile', channel=s['channel'])
    return 'sent'


async def _linkedin_turn(queue: list[dict], acct: dict, rules: dict[str, Rules], now: datetime) -> str:
    """One Unipile account, at most one action: the first queued send its window and caps allow. Its limits count
    what it sent, whichever project for."""
    if acct['last_used_at'] and now < acct['last_used_at'] + sending.send_gap(str(acct['id']), acct['last_used_at']):
        return 'paced'
    if not sending.linkedin_window_open(now):
        return 'outside_window'
    day = sending.day_start(now, sending.LINKEDIN_TIMEZONE)
    with get_conn() as conn:
        used = conn.execute(
            """SELECT count(*) FILTER (WHERE s.channel = 'linkedin_connect' AND s.attempted_at >= %s) AS invites,
                      count(*) FILTER (WHERE s.channel = 'linkedin_connect') AS week_invites,
                      count(*) FILTER (WHERE s.channel = 'linkedin_message' AND s.attempted_at >= %s) AS dms
               FROM outreach_campaign_sends s
               WHERE s.sender_id = %s AND s.channel = ANY(%s) AND s.status IN ('sending', 'sent')
                 AND s.attempted_at >= %s""",
            (day, day, acct['id'], list(LINKEDIN_CHANNELS), now - timedelta(days=7)),
        ).fetchone()
        used['dms'] += sending.linkedin_replies_today(conn, acct['id'], now)
    held, tz = 'outside_window', settings.outreach_send_window_timezone
    for s in queue:
        days = list(s['send_window_days'])
        start, end = s['send_window_start_hour'], s['send_window_end_hour']
        if not sending.in_window(now, timezone=tz, start_hour=start, end_hour=end, days=days):
            continue
        if s['channel'] == 'linkedin_connect':
            capped = used['invites'] >= sending.LINKEDIN_DAILY_INVITES
            capped |= used['week_invites'] >= sending.LINKEDIN_WEEKLY_INVITES
        else:
            capped = used['dms'] >= sending.LINKEDIN_DAILY_DMS
        if capped:
            held = 'capped'
            continue
        return await _deliver_linkedin(s, acct, rules[str(s['project_id'])], now)
    return held


async def send_linkedin_due(now: datetime) -> Counter:
    """Like ``send_due``: each connected Unipile account gets its own head-of-queue. A project without
    one has no queue at all: its rows wait, no attempt spent, until someone connects one."""
    counts: Counter = Counter()
    if is_disabled('channel_disabled:linkedin') or is_disabled('vendor_disabled:unipile'):
        counts['linkedin_disabled'] += 1
        return counts
    if not UnipileClient().is_configured():
        counts['linkedin_not_configured'] += 1
        return counts
    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT a.id AS account_row_id, q.*
               FROM user_oauth_connections a
               CROSS JOIN LATERAL (
                   SELECT s.*, c.project_id, c.send_window_start_hour, c.send_window_end_hour, c.send_window_days,
                          t.email, t.company_name
                   FROM project_senders ps
                   JOIN outreach_campaigns c ON c.project_id = ps.project_id
                   JOIN outreach_campaign_sends s ON s.campaign_id = c.id AND s.owner_id IS NOT DISTINCT FROM c.owner_id
                   JOIN outreach_campaign_targets t ON t.id = s.target_id AND t.owner_id IS NOT DISTINCT FROM s.owner_id
                   WHERE ps.sender_id = a.id AND s.channel = ANY(%s)
                     AND s.status IN ('approved', 'failed') AND s.attempts < %s
                     AND c.status = 'active' AND t.status = 'active' AND t.next_step = s.step
                     {GATE_SEND} AND {_NOT_HELD}
                     AND {_OWNER_NOT_BANNED}
                   ORDER BY s.approved_at NULLS LAST, s.created_at
                   LIMIT %s
               ) q
               WHERE a.provider = 'unipile' AND a.status = 'connected' AND NOT a.paused
                 AND a.provider_account_id IS NOT NULL""",
            (list(LINKEDIN_CHANNELS), sending.MAX_ATTEMPTS, now, QUEUE_PER_MAILBOX),
        ).fetchall()
        accounts = {
            a['id']: a
            for a in conn.execute(
                'SELECT * FROM user_oauth_connections WHERE id = ANY(%s)', (list({r['account_row_id'] for r in rows}),)
            ).fetchall()
        }
        rules = {p: load_rules(conn, p) for p in {str(r['project_id']) for r in rows}}
    queues: dict = {}
    for r in rows:
        queues.setdefault(r['account_row_id'], []).append(r)

    async def turn(acct: dict, queue: list[dict]) -> str:
        try:
            return await _linkedin_turn(queue, acct, rules, now)
        except Exception as exc:  # one account must not stop the others
            logger.error('campaign.send.linkedin_failed', user_id=str(acct['user_id']), error_type=type(exc).__name__)
            return 'errors'

    for outcome in await asyncio.gather(*(turn(accounts[k], q) for k, q in queues.items())):
        counts[f'linkedin_{outcome}'] += 1
    return counts


def complete_finished() -> int:
    """Active campaigns whose sequences have all ended become ``completed`` (#218).

    Each candidate is locked, then checked again in a fresh statement: an undo of a reject
    (``routes.undo_reject``) holds the campaign lock while it puts a person back, and a check made
    before that lock would still see the person gone and complete a campaign with a message waiting.
    Only campaign rows are locked here, never targets, so the undo's campaign-then-targets order cannot deadlock;
    NO KEY UPDATE, like the undo, so a claim's foreign-key KEY SHARE on the campaign never waits on it.
    """
    with get_conn() as conn:
        candidates = [
            r['id']
            for r in conn.execute(f"SELECT c.id FROM outreach_campaigns c WHERE c.status = 'active' AND {FINISHED}")
        ]
    completed = 0
    for campaign_id in candidates:
        with get_conn() as conn:
            if not conn.execute(
                "SELECT 1 FROM outreach_campaigns WHERE id = %s AND status = 'active' FOR NO KEY UPDATE", (campaign_id,)
            ).fetchone():
                continue
            done = conn.execute(
                f"UPDATE outreach_campaigns c SET status = 'completed', updated_at = now() WHERE c.id = %s AND {FINISHED}"
                ' RETURNING c.id, c.owner_id, c.name',
                (campaign_id,),
            ).fetchone()
        if done:
            completed += 1
            webhooks.emit('campaign.finished', {'campaign_id': str(done['id']), 'name': done['name']})
    return completed
