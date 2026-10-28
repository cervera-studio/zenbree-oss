"""Notifications (#173): every event lands in the bell panel; email and browser follow the user's settings.

Rules (handoff P2, E3, E4):
- Only events the system causes are notified: the user's own actions get the page's toast. A search the
  user is watching (shorter than ``SEARCH_EMAIL_AFTER`` with a Zenbree tab open) is one of them.
- Urgent reply emails (interested, meeting request) go out at most once per ``URGENT_EMAIL_EVERY`` per user;
  the overflow, the other replies and anything held by do-not-disturb wait for the digest, which each user
  schedules (every working day or Mondays, at their hour in ``DEFAULT_TIMEZONE``).
- No email carries a reply's text; the search-done one carries no person names.
- A kind whose email is ``always`` (``KINDS``: payments, a mailbox in error, bounces, low credits) has no toggle. Security emails are Better Auth's, sent by the web.
- Every email has a text and an HTML part in the shared layout (``layout``, #221), in the recipient's
  ``"user".locale`` (#377), English when unknown (``app.core.locale``).

Email goes through Resend (``RESEND_API_KEY`` / ``EMAIL_FROM``); either empty makes the channel inert.
Every function takes the caller's connection, so the row and the rule state commit with the caller's work;
email and the push to open tabs go out afterwards (``deliver``), never inside the caller's transaction.

Open tabs (#242) tail the Redis stream ``stream_key(user)`` over SSE (``routes.stream``); while one is
connected the server keeps ``open_key(user)`` alive, which is what "a Zenbree tab is open" means. A browser
alert for a closed tab goes out as Web Push (``push``, #343) in the same ``deliver`` call.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
import structlog
from psycopg.types.json import Jsonb

from app.agent import events
from app.billing.balance import NET_CREDITS_SQL
from app.core.config import settings
from app.core.db import get_conn
from app.core.locale import DEFAULT_LOCALE, Locale, stored_locale
from app.core.scope import ActorGone, acting_as
from app.core.working_window import DEFAULT_TIMEZONE
from app.notifications import emails, layout, push
from app.notifications.emails.facts import LOW_CREDITS as LOW_CREDITS
from app.notifications.emails.facts import Digest as Digest
from app.notifications.hrefs import switch_href
from app.notifications.quotes import with_quotes
from app.outreach import approval_queue
from app.outreach.approval_queue import NO_APPROVALS

logger = structlog.get_logger()


@dataclass(frozen=True)
class Kind:
    pref: str | None
    email: Literal['urgent', 'now', 'digest', 'always', 'never']
    browser: bool


KINDS: dict[str, Kind] = {
    'reply_urgent': Kind('reply_urgent', 'urgent', True),
    'reply': Kind('reply', 'digest', True),
    'linkedin_accepted': Kind('linkedin_accepted', 'digest', True),
    'approval_needed': Kind('approval_needed', 'digest', True),
    'search_done': Kind('search_done', 'now', True),
    'search_failed': Kind('search_failed', 'urgent', True),  # P2: a stopped search is urgent (#215)
    'agent_question': Kind('search_failed', 'never', True),
    'bounces_high': Kind('bounces_high', 'always', True),
    'low_credits': Kind('low_credits', 'always', True),
    'mailbox_error': Kind(None, 'always', False),
    'payment_failed': Kind(None, 'always', False),
    'plan_renewed': Kind(None, 'never', False),
}
ROW_DEFAULTS: dict[str, dict[str, bool]] = {
    'reply_urgent': {'email': True, 'browser': True},
    'reply': {'email': True, 'browser': False},
    'linkedin_accepted': {'email': False, 'browser': False},
    'approval_needed': {'email': True, 'browser': False},
    'search_done': {'email': False, 'browser': False},  # E4: off by default
    'search_failed': {'email': True, 'browser': True},
    'bounces_high': {'browser': False},
    'low_credits': {'browser': True},
}
URGENT_EMAIL_EVERY = timedelta(minutes=30)
SEARCH_EMAIL_AFTER = timedelta(minutes=5)
STREAM_MAXLEN = 200  # replay depth for a reconnecting tab; the panel reload covers anything older
STREAM_TTL = timedelta(days=1)
OPEN_TTL_SECONDS = 45  # three missed 15 s stream pings
DEFAULT_DIGEST_HOUR = 9

# #323: notifications stay the user's, but one about an organization they have left is no longer theirs to see. Banned
# counts as left: the digest reads these as System, where no policy hides a banned member's organizations.
VISIBLE_SQL = '((n.user_id, n.organization_id) IN (SELECT user_id, organization_id FROM zb.live_members))'


def org_name(conn, org_id: str) -> str:
    row = conn.execute('SELECT name FROM organization WHERE id = %s', (org_id,)).fetchone()
    return row['name'] if row else ''


def _email_on() -> bool:
    return bool(settings.resend_api_key and settings.email_from)


def prefs(conn, user_id: str) -> dict:
    row = conn.execute(
        'SELECT events, digest_frequency, digest_hour, dnd FROM notification_settings WHERE user_id = %s', (user_id,)
    ).fetchone()
    saved = (row or {}).get('events') or {}
    return {
        'events': {
            k: {**v, **{c: x for c, x in (saved.get(k) or {}).items() if c in v}} for k, v in ROW_DEFAULTS.items()
        },
        'digest': {
            'frequency': row['digest_frequency'] if row else 'daily',
            'hour': row['digest_hour'] if row else DEFAULT_DIGEST_HOUR,
        },
        'dnd': row['dnd'] if row else False,
    }


def browser_on(p: dict, kind: str) -> bool:
    k = KINDS[kind]
    return not p['dnd'] and k.browser and k.pref is not None and p['events'][k.pref]['browser']


def unread_count(conn, user_id: str) -> int:
    return conn.execute(
        f'SELECT count(*) AS n FROM notifications n WHERE n.user_id = %s AND n.read_at IS NULL AND {VISIBLE_SQL}',
        (user_id,),
    ).fetchone()['n']


def unread_replies(conn, user_id: str, org_id: str) -> tuple[int, dict | None]:
    rows = conn.execute(
        'SELECT data, created_at FROM notifications n WHERE n.user_id = %s AND n.read_at IS NULL'
        ' AND n.organization_id = %s'
        f" AND n.kind IN ('reply', 'reply_urgent') AND {VISIBLE_SQL} ORDER BY n.created_at DESC",
        (user_id, org_id),
    ).fetchall()
    return len(rows), ({**rows[0]['data'], 'at': rows[0]['created_at']} if rows else None)


def save_prefs(conn, user_id: str, events: dict, digest: dict, dnd: bool) -> None:
    conn.execute(
        """INSERT INTO notification_settings (user_id, events, digest_frequency, digest_hour, dnd)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (user_id) DO UPDATE SET events = EXCLUDED.events,
               digest_frequency = EXCLUDED.digest_frequency, digest_hour = EXCLUDED.digest_hour,
               dnd = EXCLUDED.dnd, updated_at = now()""",
        (
            user_id,
            Jsonb({k: v for k, v in events.items() if k in ROW_DEFAULTS}),
            digest['frequency'],
            digest['hour'],
            dnd,
        ),
    )
    if digest['frequency'] == 'off':  # what queued while it was on would otherwise arrive weeks later, stale
        conn.execute('UPDATE notifications SET email_pending = false WHERE user_id = %s AND email_pending', (user_id,))


def _claim_urgent_slot(conn, user_id: str) -> bool:
    """True once per URGENT_EMAIL_EVERY per user; the claim and the check are one statement."""
    return (
        conn.execute(
            """INSERT INTO notification_settings (user_id, last_urgent_email_at) VALUES (%s, now())
               ON CONFLICT (user_id) DO UPDATE SET last_urgent_email_at = now()
               WHERE notification_settings.last_urgent_email_at IS NULL
                  OR notification_settings.last_urgent_email_at < now() - %s
               RETURNING user_id""",
            (user_id, URGENT_EMAIL_EVERY),
        ).fetchone()
        is not None
    )


def _email_mode(conn, user_id: str, kind: str, p: dict) -> str | None:
    """'now', 'digest' or None (no email)."""
    k = KINDS[kind]
    if not _email_on() or k.email == 'never':
        return None
    if k.email == 'always':
        return 'now'
    if not p['events'][k.pref]['email']:
        return None
    later = 'digest' if p['digest']['frequency'] != 'off' else None
    if k.email == 'digest' or p['dnd']:
        return later
    if k.email == 'urgent' and not _claim_urgent_slot(conn, user_id):
        return later
    return 'now'


@dataclass(frozen=True)
class Notified:
    id: str
    user_id: str
    org_id: str  # the organization it is about: deliver acts as the recipient there
    item: dict  # the panel item (GET /v1/notifications ``items[]``) pushed to open tabs
    mail: tuple[str, layout.Email] | None = None  # (to, email) to send once the caller has committed
    locale: Locale = DEFAULT_LOCALE  # the recipient's, for the browser alert


def notify(conn, user_id: str, kind: str, *, href: str, data: dict | None = None, org_id: str) -> Notified | None:
    """Store one notification (queued for the digest when that is its email) in the caller's transaction.
    ``org_id``: the organization it is about, never None; the user stops seeing it once they leave that
    organization, and nothing is stored for one who is not, or no longer, its member (#412). Sends nothing:
    pass the result to ``deliver`` after the commit."""
    if not conn.execute('SELECT zb.is_live_member(%s, %s) AS ok', (user_id, org_id)).fetchone()['ok']:
        return None
    data = data or {}
    p = prefs(conn, user_id)
    mode = _email_mode(conn, user_id, kind, p)
    row = conn.execute(
        """INSERT INTO notifications (user_id, organization_id, kind, href, data, email_pending)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id, created_at""",
        (user_id, org_id, kind, href, Jsonb(data), mode == 'digest'),
    ).fetchone()
    nid = str(row['id'])
    item = {
        'id': nid,
        'kind': kind,
        'href': switch_href(org_id, href),
        'data': data,
        'read': False,
        'created_at': row['created_at'].isoformat(),
        'browser': browser_on(p, kind),
    }
    with_quotes(conn, user_id, [item])
    to, locale = recipient(conn, user_id) or (None, DEFAULT_LOCALE)  # the browser alert needs the locale in any mode
    mail = (to, _render(kind, switch_href(org_id, href), data, locale, org_id)) if mode == 'now' and to else None
    return Notified(nid, user_id, org_id, item, mail, locale)


def deliver(notified: Notified | None) -> None:
    """Push a committed notification to open tabs and browsers and send its email, as its recipient: the pushed unread
    count is then the bell's, of every organization where they are a live member. Call it with no connection open. A
    recipient who left the organization since gets nothing. Never raises: a failure is logged, the panel has the item."""
    if notified is None:
        return
    try:
        with acting_as(notified.org_id, notified.user_id):
            _send(notified)
    except ActorGone:  # a BaseException: it must not end the caller's loop over the other recipients
        logger.info('notification.recipient_gone', notification_id=notified.id)


def _send(notified: Notified) -> None:
    try:
        with get_conn() as conn:  # the first checkout as the recipient: ActorGone when they left, before any push
            unread = unread_count(conn, notified.user_id)
        publish(notified.user_id, 'notification', {'item': notified.item, 'unread': unread})
    except Exception as exc:
        logger.error('notification.publish_failed', notification_id=notified.id, error_type=type(exc).__name__)
    if notified.item['browser']:
        try:
            push.send(notified.user_id, push.payload(notified.item, notified.locale))
        except Exception as exc:
            logger.error('notification.push_failed', notification_id=notified.id, error_type=type(exc).__name__)
    if notified.mail is None:
        return
    try:
        to, mail = notified.mail
        if send_email(to, mail.subject, mail.text, mail.html, kind=notified.item['kind']):
            with get_conn() as conn:
                conn.execute('UPDATE notifications SET emailed_at = now() WHERE id = %s', (notified.id,))
    except Exception as exc:  # not logger.exception: the frame locals hold the address and the email
        logger.error('notification.deliver_failed', notification_id=notified.id, error_type=type(exc).__name__)


# ── open tabs (#242) ─────────────────────────────────────────────────────────


def stream_key(user_id: str) -> str:
    return f'user:{user_id}:notifications'


def open_key(user_id: str) -> str:
    return f'user:{user_id}:open'


def publish(user_id: str, event: str, data: dict) -> None:
    """Append a committed change to the user's stream. Never raises: an open tab misses it until a reload."""
    events.append(stream_key(user_id), event, data, maxlen=STREAM_MAXLEN, ttl=STREAM_TTL)


def stream_cursor(user_id: str) -> str | None:
    """The user's newest stream entry id ('0-0' when there is none); None when Redis is unreachable."""
    return events.last_id(stream_key(user_id))


def mark_open(user_id: str) -> None:
    try:
        events.redis_sync().set(open_key(user_id), 1, ex=OPEN_TTL_SECONDS)
    except Exception as exc:
        logger.error('notification.mark_open_failed', error_type=type(exc).__name__)


def tab_open(user_id: str) -> bool:
    """A Zenbree tab holds the bell stream. Unknown (Redis down) counts as closed: notify rather than miss."""
    try:
        return bool(events.redis_sync().exists(open_key(user_id)))
    except Exception as exc:
        logger.error('notification.tab_open_failed', error_type=type(exc).__name__)
        return False


def recipient(conn, user_id: str) -> tuple[str, Locale] | None:
    row = conn.execute('SELECT email, locale FROM "user" WHERE id = %s', (user_id,)).fetchone()
    return (row['email'], stored_locale(row['locale'])) if row and row['email'] else None


def _render(kind: str, href: str, data: dict, locale: Locale, org_id: str = '') -> layout.Email:
    return emails.render(kind, href, data, locale, org_id)


def send_email(to: str, subject: str, text: str, html: str | None = None, *, kind: str = '') -> bool:
    """``kind`` names the email in the failure log: the subject may carry a list, client or person name."""
    sent = False
    for attempt in range(3):
        try:
            resp = httpx.post(
                'https://api.resend.com/emails',
                headers={'Authorization': f'Bearer {settings.resend_api_key}'},
                json={'from': settings.email_from, 'to': [to], 'subject': subject, 'text': text}
                | ({'html': html} if html else {}),
                timeout=10,
            )
        except httpx.HTTPError:
            break
        if resp.status_code != 429:
            sent = resp.status_code < 300
            break
        time.sleep(attempt + 1)  # Resend's rate limit (2 requests/s by default)
    if not sent:  # ponytail: no retry beyond a 429; the notification is in the panel either way
        logger.warning('notification.email_failed', kind=kind)
    return sent


# ── end of a chat turn (E4, #215) and messages held for approval ─────────────

# Stage error codes (docs/contracts-m1.md) of a run that stopped on its provider or a crash; the credit
# ones (no_credits, over_budget) are the chat's own card.
STOPPED = frozenset({'provider', 'failed', 'crashed', 'disabled'})


def search_done_due(took: timedelta, tab_is_open: bool) -> bool:
    """Long enough to have walked away, or no Zenbree tab open: otherwise the chat itself is enough."""
    return took > SEARCH_EMAIL_AFTER or not tab_is_open


def turn_outcome(
    status: str,
    stages: list[dict],
    question: dict | None,
    found_rows: bool,
    refunded: int,
    spent: int = 0,
    undelivered: int = 0,
) -> tuple[str, dict] | None:
    """What the bell says about a finished turn: a stage its provider stopped, a question waiting for the
    user, or rows found. ``None``: nothing worth a notification. ``spent``: the turn's credits net of
    ``refunded``, given back for its ``undelivered`` rows."""
    stopped = next((s for s in stages if (s.get('error') or {}).get('code') in STOPPED), None)
    if stopped:
        return 'search_failed', {'done': min(stopped['done'], stopped['units']), 'units': stopped['units']}
    if status == 'needs_input' and question:
        reason = 'plan' if question.get('plan') is not None else 'funnel' if question.get('funnel') else None
        return 'agent_question', {'reason': reason}
    if status == 'succeeded' and found_rows:
        return 'search_done', {'refunded': refunded, 'spent': spent, 'undelivered': undelivered}
    return None


def notify_turn(
    conn, user_id: str, session_id: str, started_at: datetime | None, outcome: tuple[str, dict] | None
) -> Notified | None:
    """``outcome`` from ``turn_outcome``, unless the user is watching the chat. Adds the chat's title and,
    for a search that found rows, its lists (size, name, id: the people one first) and the balance after."""
    now = datetime.now(UTC)
    if outcome is None or not search_done_due(now - (started_at or now), tab_open(user_id)):
        return None
    kind, data = outcome
    # By id only: the job proves the chat, and a teammate's turn runs on a chat its creator made (#702).
    chat = conn.execute(
        'SELECT title, organization_id::text AS organization_id FROM chat_sessions WHERE id = %s', (session_id,)
    ).fetchone()
    if chat is None:
        return None
    data = {**data, 'chat': (chat['title'] or '')[:80]}
    if kind == 'search_done':
        for r in conn.execute(
            """SELECT l.id::text, l.name, l.entity, count(r.id) AS n FROM lists l
               LEFT JOIN list_rows r ON r.list_id = l.id
               WHERE l.chat_session_id = %s GROUP BY l.id ORDER BY l.entity = 'person'""",
            (session_id,),
        ):
            data['people' if r['entity'] == 'person' else 'companies'] = r['n']
            data |= {'list_id': r['id'], 'list': r['name'][:80]}  # the people list wins: it comes last
            if r['entity'] == 'person':
                data['people_list_id'] = r['id']
        data['balance'] = conn.execute(
            f'SELECT {NET_CREDITS_SQL.format(org="%s")} AS n', (chat['organization_id'],)
        ).fetchone()['n']
    return notify(conn, user_id, kind, href=f'/?s={session_id}', data=data, org_id=chat['organization_id'])


def notify_approvals(conn, user_id: str, campaign_id: str) -> Notified | None:
    """A campaign holds messages for approval (``approve_before_sending``): the dispatcher calls this when a
    beat held new ones. Once while the item is unread, and not while the user watches the campaign they
    started a moment ago."""

    href = f'/campaigns/{campaign_id}'
    c = conn.execute(
        """SELECT c.id, c.project_id, c.name, c.activated_at, c.organization_id::text
            FROM outreach_campaigns c
            WHERE c.id = %s AND c.owner_id = %s
              AND NOT EXISTS (SELECT 1 FROM notifications WHERE user_id = c.owner_id AND kind = 'approval_needed'
                              AND href = %s AND read_at IS NULL)""",
        (campaign_id, user_id, href),
    ).fetchone()
    now = datetime.now(UTC)
    if not c or not search_done_due(now - (c['activated_at'] or now), tab_open(user_id)):
        return None
    n = approval_queue.counts(conn, c['project_id'], c['id']).get(c['id'], NO_APPROVALS).awaiting
    if not n:
        return None
    # ponytail: the count is the one at the first beat; the campaign page shows the live queue.
    return notify(
        conn,
        user_id,
        'approval_needed',
        href=href,
        data={'count': n, 'campaign': c['name']},
        org_id=c['organization_id'],
    )


CONNECTION_FIELDS = (
    'user_id::text',
    'organization_id::text',
    'provider',
    'account_label',
    'linkedin_account_name',
    'display_name',
)

BOUNCE_MIN_ATTEMPTS = 20
BOUNCE_RATE_PERCENT = 5


def notify_mailbox_error(conn, c: dict) -> Notified | None:
    """A sender went from connected to error (the provider refused it, or Unipile says the account is
    unhealthy), never the user's own disconnect. ``c``: that row, selected as ``CONNECTION_FIELDS``."""
    if c['user_id'] is None:  # #333: the person who connected it is gone
        return None
    label = c['account_label'] or c['linkedin_account_name'] or c['display_name'] or ''
    return notify(
        conn,
        c['user_id'],
        'mailbox_error',
        href='/settings/integrations',
        data={'mailbox': label, 'provider': c['provider']},
        org_id=c['organization_id'],
    )


def notify_bounces(conn, campaign_id: str) -> Notified | None:
    """Once per campaign, when the campaign planned to write to at least BOUNCE_MIN_ATTEMPTS people (planned
    or sent, plus the addresses refused) and more than BOUNCE_RATE_PERCENT of them were refused before sending
    (INVALID_EMAIL): nothing went to them, so no pause. The bounces read from the mailbox (#747) pause the campaign
    and notify on their own (app.outreach.bounces)."""
    href = f'/campaigns/{campaign_id}'
    conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (href,))  # two beats, one notification
    c = conn.execute(
        """SELECT c.name, c.owner_id::text, c.organization_id::text AS org_id, x.invalid, x.attempted
           FROM outreach_campaigns c
           CROSS JOIN LATERAL (
               SELECT count(DISTINCT s.target_id) FILTER (WHERE s.error_code = 'INVALID_EMAIL') AS invalid,
                      count(DISTINCT s.target_id) AS attempted
               FROM outreach_campaign_sends s
               WHERE s.campaign_id = c.id AND s.channel = 'email' AND s.target_id IS NOT NULL
                 AND (s.status NOT IN ('skipped', 'refused') OR s.error_code = 'INVALID_EMAIL')) x
           WHERE c.id = %s AND c.owner_id IS NOT NULL AND x.attempted >= %s AND x.invalid * 100 > x.attempted * %s
             AND NOT EXISTS (SELECT 1 FROM notifications WHERE user_id = c.owner_id AND kind = 'bounces_high'
                             AND href = %s)""",
        (campaign_id, BOUNCE_MIN_ATTEMPTS, BOUNCE_RATE_PERCENT, href),
    ).fetchone()
    if not c:
        return None
    return notify(
        conn,
        c['owner_id'],
        'bounces_high',
        href=href,
        data={'campaign': c['name'], 'invalid': c['invalid'], 'attempted': c['attempted']},
        org_id=c['org_id'],
    )


# ── digest (beat) ────────────────────────────────────────────────────────────


def digest_due(frequency: str, hour: int, now: datetime) -> bool:
    """The hourly beat sends at the user's hour in DEFAULT_TIMEZONE: every working day, or on Monday."""
    local = now.astimezone(ZoneInfo(DEFAULT_TIMEZONE))
    # ponytail: Italian public holidays still get a digest. No per-user time zone is stored.
    on_day = local.weekday() < 5 if frequency == 'daily' else local.weekday() == 0 if frequency == 'weekly' else False
    return on_day and local.hour == hour


def take_digests(conn, now: datetime) -> list[Digest]:
    """The digests due at ``now``: users whose schedule hits this hour and who have queued items. Their queue
    is taken before sending; the live lines are read here, at send time."""
    if not _email_on():
        return []

    waiting = conn.execute(
        f"""SELECT DISTINCT u.id::text AS user_id, u.email, u.name, u.locale,
                   coalesce(s.digest_frequency, 'daily') AS frequency, coalesce(s.digest_hour, %s) AS hour
            FROM notifications n JOIN "user" u ON u.id = n.user_id
            LEFT JOIN notification_settings s ON s.user_id = n.user_id
            WHERE n.email_pending AND {VISIBLE_SQL}""",
        (DEFAULT_DIGEST_HOUR,),
    ).fetchall()
    due = {w['user_id']: w for w in waiting if digest_due(w['frequency'], w['hour'], now)}
    if not due:
        return []
    # ponytail: a failed send drops that digest; the panel still has every item.
    taken = conn.execute(
        f"""UPDATE notifications n SET email_pending = false, emailed_at = now()
           WHERE n.email_pending AND n.user_id = ANY(%s::uuid[]) AND {VISIBLE_SQL}
           RETURNING n.user_id::text, n.kind""",
        (list(due),),
    ).fetchall()
    queued: dict[str, dict[str, int]] = {}
    for r in taken:
        per_kind = queued.setdefault(r['user_id'], {})
        per_kind[r['kind']] = per_kind.get(r['kind'], 0) + 1
    local = now.astimezone(ZoneInfo(DEFAULT_TIMEZONE))
    digests = []
    for uid, w in due.items():
        if uid not in queued:
            continue
        approvals = 0
        if prefs(conn, uid)['events']['approval_needed']['email']:
            approvals = approval_queue.owned_awaiting(conn, uid)
        balances = conn.execute(
            """SELECT o.name, bal.credits_remaining AS credits FROM member m
               JOIN organization o ON o.id = m."organizationId"
               JOIN user_credits bal ON bal.organization_id = o.id
               WHERE m."userId" = %s AND m.role = 'admin' ORDER BY o.name""",
            (uid,),
        ).fetchall()
        digest = Digest(
            w['email'],
            w['name'],
            w['frequency'],
            queued[uid],
            int(approvals),
            [(b['name'], b['credits']) for b in balances],
            local,
            stored_locale(w['locale']),
        )
        if digest.has_news:  # the approvals it queued for were handled since: nothing left to say
            digests.append(digest)
    return digests


def send_digest(d: Digest) -> bool:
    mail = emails.digest(d)
    return send_email(d.email, mail.subject, mail.text, mail.html, kind='digest')
