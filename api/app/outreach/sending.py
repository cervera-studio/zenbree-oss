"""Pure send rules: when a step is due, what a mailbox may send, and the final copy.

Ported from the earlier implementation ``integrations/domain/send_policy.py`` (cap, warm-up, gap),
``outreach/domain/personalisation.py`` (contact tokens), ``domain/opt_out.py``,
``integrations/domain/sign_off.py``, and the LinkedIn caps/note limits of the earlier implementation ``outreach/domain/linkedin.py``
and ``invitation_note.py``. No clock: ``now`` is passed in. The only I/O is ``sent_today``, through the
caller's connection.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, time, timedelta
from typing import cast
from urllib.parse import quote, unquote, urlparse

from app.core.config import settings
from app.core.errors import Result, err, ok
from app.core.reply_address import build_campaign_reply_address, build_reply_address
from app.core.working_window import timezone_or_default
from app.exclusions.rules import normalise

# Owner decision on #80: 50/day per mailbox with warm-up. Gmail's own ceiling is 500 (2000 Workspace).
DAILY_SEND_LIMIT = 50
RAMP = ((timedelta(days=7), 10), (timedelta(days=14), 25))

# Random-looking gap between two automatic sends from one mailbox: a fixed cadence is a signature.
MIN_GAP_SECONDS = 9 * 60
MAX_GAP_SECONDS = 14 * 60

# A failed attempt that surely did not deliver is retried this many times in total.
MAX_ATTEMPTS = 3

# Storage limits of outreach_campaign_sends: a rendered copy over them is refused, never truncated.
MAX_SUBJECT = 300
MAX_BODY = 4000

# A provider rate limit pauses the whole mailbox this long instead of spending a row's attempt.
RATE_LIMIT_PAUSE = timedelta(hours=1)

# Owner decision on #80: per LinkedIn account, and only 9-18 Mon-Fri in Rome whatever the campaign says.
LINKEDIN_DAILY_INVITES = 20
LINKEDIN_WEEKLY_INVITES = 100
LINKEDIN_DAILY_DMS = 30
LINKEDIN_TIMEZONE = 'Europe/Rome'
# A DM waits this long for the invitation to be accepted, then is skipped so the email steps go on (as the earlier implementation).
LINKEDIN_ACCEPT_WAIT = timedelta(days=3)
# LinkedIn's own allowance refused us: the whole account rests this long (the earlier implementation linkedin_pacing).
LINKEDIN_LIMIT_COOLDOWN = timedelta(hours=24)
NOTE_MAX_PREMIUM = 300
NOTE_MAX_FREE = 200

# Reply-footer opt-out (#75, as the earlier implementation). Idempotent; appended to every campaign email on the wire.
# The handoff's Unsub-Footer (#220): with a link, «Oppure: <url>» sits between the two sentences.
EMAIL_OPT_OUT = (
    'Per non ricevere altri messaggi, è sufficiente rispondere "no grazie" e non verranno inviate altre email.'
)
EMAIL_GDPR = 'Trattamento dati ai sensi del GDPR Art. 13 e D.Lgs. 196/2003.'

CONTACT_TOKENS = {
    'firstName': 'first_name',
    'first_name': 'first_name',
    'lastName': 'last_name',
    'last_name': 'last_name',
    'companyName': 'company_name',
    'company_name': 'company_name',
    'company': 'company_name',
    'role': 'role',
    'title': 'role',
    'email': 'email',
}

_PROFILE_SLUG = re.compile(r'linkedin\.com/in/([^/?#\s]+)', re.IGNORECASE)
_TOKEN = re.compile(r'\{\{[^{}]*\}\}')
_PLACEHOLDER = re.compile(r'\[[^\]\n]{2,40}\]|\{\{[^}\n]{2,40}\}\}|\{\{|\}\}')


def daily_cap(daily_limit: int | None, connected_since: datetime | None, now: datetime) -> int:
    """The mailbox's own limit (1-50, default 50), lowered while it warms up: 10/day week one, 25 week two.

    Unknown age counts as brand new: "I do not know how long you have been sending" means "not long".
    """
    cap = min(daily_limit or DAILY_SEND_LIMIT, DAILY_SEND_LIMIT)
    age = now - connected_since if connected_since else timedelta(0)
    for until, limit in RAMP:
        if age < until:
            return min(cap, limit)
    return cap


def send_gap(mailbox_id: str, last_sent: datetime) -> timedelta:
    """9-14 minutes, deterministic per (mailbox, last send) so every worker agrees and a test can assert it."""
    seed = f'{mailbox_id}:{last_sent.astimezone(UTC).isoformat()}'.encode()
    spread = int.from_bytes(hashlib.blake2b(seed, digest_size=8).digest(), 'big')
    return timedelta(seconds=MIN_GAP_SECONDS + spread % (MAX_GAP_SECONDS - MIN_GAP_SECONDS + 1))


def in_window(now: datetime, *, timezone: str, start_hour: int, end_hour: int, days: list[int]) -> bool:
    """Campaign hours and weekdays (Monday=0), judged in the mailbox's own zone."""
    local = now.astimezone(timezone_or_default(timezone))
    return local.weekday() in days and start_hour <= local.hour < end_hour


def window_open(row: dict, now: datetime, timezone: str) -> bool:
    """A campaign's or a mailbox's ``send_window_*`` columns, judged in the mailbox's zone."""
    return in_window(
        now,
        timezone=timezone,
        start_hour=row['send_window_start_hour'],
        end_hour=row['send_window_end_hour'],
        days=list(row['send_window_days']),
    )


def linkedin_window_open(now: datetime) -> bool:
    return in_window(now, timezone=LINKEDIN_TIMEZONE, start_hour=9, end_hour=18, days=[0, 1, 2, 3, 4])


def day_start(now: datetime, timezone: str) -> datetime:
    """Local midnight in the mailbox's zone: the daily cap resets there, not at UTC midnight."""
    local = now.astimezone(timezone_or_default(timezone))
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def sent_today(conn, sender_id, now: datetime, timezone: str) -> int:
    """Attempts this mailbox made since local midnight, campaign steps and inbox replies (#187) alike, whichever
    project they were for (#307): what its daily cap is held against."""
    since = day_start(now, timezone)
    return conn.execute(
        'SELECT (SELECT count(*) FROM outreach_campaign_sends WHERE sender_id = %(m)s'
        "        AND status IN ('sending', 'sent') AND attempted_at >= %(since)s)"
        ' + (SELECT count(*) FROM outreach_inbox_replies WHERE sender_id = %(m)s'
        "        AND status IN ('sending', 'sent') AND created_at >= %(since)s) AS n",
        {'m': sender_id, 'since': since},
    ).fetchone()['n']


def at_cap(conn, channel: str, sender_id, now: datetime) -> bool:
    """Whether the sender may take no more of this ``channel`` today, counted under a lock on its row. The lock
    lasts to the caller's commit, so the caller claims its send in the same transaction: two contenders (campaign
    steps and inbox replies alike) for one remaining allowance cannot both count it free. A sender that is gone
    is capped. Call it before any provider I/O and keep the transaction short."""
    row = conn.execute(
        'SELECT daily_limit, created_at, send_timezone FROM user_oauth_connections WHERE id = %s FOR NO KEY UPDATE',
        (sender_id,),
    ).fetchone()
    if row is None:
        return True
    if channel == 'email':
        tz = row['send_timezone'] or settings.outreach_send_window_timezone
        return sent_today(conn, sender_id, now, tz) >= daily_cap(row['daily_limit'], row['created_at'], now)
    day = day_start(now, LINKEDIN_TIMEZONE)
    used = conn.execute(
        'SELECT count(*) FILTER (WHERE channel = %s AND attempted_at >= %s) AS today, count(*) AS week'
        " FROM outreach_campaign_sends WHERE sender_id = %s AND channel = %s AND status IN ('sending', 'sent')"
        ' AND attempted_at >= %s',
        (channel, day, sender_id, channel, now - timedelta(days=7)),
    ).fetchone()
    if channel == 'linkedin_connect':
        return used['today'] >= LINKEDIN_DAILY_INVITES or used['week'] >= LINKEDIN_WEEKLY_INVITES
    return used['today'] + linkedin_replies_today(conn, sender_id, now) >= LINKEDIN_DAILY_DMS


def step_due_at(steps: list[dict], step: int, anchor: datetime) -> datetime:
    """Step 1 counts from activation; step N from when step N-1 went out, by the difference in delays.

    Measured from the previous delivery, not from activation, so a person enrolled late or held
    by a pause gets the same spacing as everyone else instead of a burst.
    """
    delay = steps[step - 1]['send_after_days'] - (steps[step - 2]['send_after_days'] if step > 1 else 0)
    return anchor + timedelta(days=delay)


def thread_root(steps: list[dict], step: int) -> int | None:
    """The email step a threaded follow-up replies under: the nearest earlier unthreaded email."""
    for earlier in range(step - 1, 0, -1):
        s = steps[earlier - 1]
        if s['channel'] == 'email' and not s.get('threaded'):
            return earlier
    return None


def render_values(template: str, values: dict[str, str]) -> Result[str]:
    tokens = _TOKEN.findall(template)
    if unknown := sorted({t for t in tokens if t[2:-2] not in values}):
        return err('OUTREACH.COPY_UNRENDERED', detail=f'unknown placeholder {unknown[0]}')
    if missing := sorted({t for t in tokens if not values[t[2:-2]].strip()}):
        return err('OUTREACH.COPY_MISSING_FIELD', detail=f'no value for {missing[0]}')
    # A contact field with a line break would otherwise reach a header (subject) or reshape the body.
    return ok(_TOKEN.sub(lambda m: ' '.join(values[m.group(0)[2:-2]].split()), template))


def fill_known(template: str, values: dict[str, str]) -> str:
    return _TOKEN.sub(lambda m: values.get(m.group(0)[2:-2], m.group(0)), template)


def single_address(raw: str | None) -> str | None:
    """Exactly one valid address, normalised, or ``None``. "a@x.com, b@y.com" is not one recipient:
    it would slip past the exclusion check and put two people on one claim."""
    value = normalise('email', raw or '')
    return value if value and not any(c in value for c in ',;<>()"\'') else None


def profile_slug(linkedin_url: str) -> str:
    """The ``/in/<slug>`` Unipile resolves, URL-quoted once; ``''`` for a company page or junk."""
    match = _PROFILE_SLUG.search(linkedin_url or '')
    return quote(unquote(match.group(1)), safe='') if match else ''


def first_degree(profile: dict) -> bool:
    """Already a connection, read off the resolved profile (both spellings Unipile has shipped)."""
    distance = str(profile.get('network_distance') or '').upper()
    return profile.get('is_relationship') is True or distance in ('FIRST_DEGREE', 'DISTANCE_1')


def invitation_note(note: str, premium: bool | None) -> str:
    """The approved note, or none when it is longer than this account may attach (unknown plan = free)."""
    return note if len(note) <= (NOTE_MAX_PREMIUM if premium else NOTE_MAX_FREE) else ''


def find_placeholder(*parts: str) -> str | None:
    """An unfilled ``[Nome]`` / ``{{x}}`` the copy still carries; checked on what will actually leave."""
    for part in parts:
        if match := _PLACEHOLDER.search(part):
            return match.group(0)
    return None


def opt_out_footer(unsubscribe_url: str | None = None) -> str:
    link = f' Oppure: {unsubscribe_url}' if unsubscribe_url else ''
    return f'{EMAIL_OPT_OUT}{link}\n{EMAIL_GDPR}'


def with_sign_off(body: str, sign_off: str) -> str:
    text = body.rstrip()
    return f'{text}\n\n{block}' if (block := sign_off.strip()) else text


def carries_footer(text: str) -> bool:
    return 'no grazie' in text and 'GDPR' in text


def compose_body(body: str, sign_off: str, unsubscribe_url: str | None = None) -> str:
    """Approved copy + the mailbox's sign-off + the opt-out footer (with the unsubscribe page, #172, when
    there is one). The two additions are system-owned."""
    text = with_sign_off(body, sign_off)
    if carries_footer(text):
        # ponytail: copy that already carries the footer goes out without the unsubscribe link (#172);
        # stored bodies never do today (the footer is added only here), the RFC 8058 header still is.
        return text
    return f'{text}\n\n---\n{opt_out_footer(unsubscribe_url)}'


def next_open(now: datetime, *, timezone: str, start_hour: int, end_hour: int, days: list[int]) -> datetime | None:
    if not days or not 0 <= start_hour < end_hour <= 24:
        return None
    if in_window(now, timezone=timezone, start_hour=start_hour, end_hour=end_hour, days=days):
        return now
    zone = timezone_or_default(timezone)
    local = now.astimezone(zone)
    for ahead in range(8):
        day = (local + timedelta(days=ahead)).date()
        opening = datetime.combine(day, time(start_hour), tzinfo=zone)
        if day.weekday() in days and opening > now:
            return opening.astimezone(UTC)
    return None


def next_send_time(now: datetime, windows: list[dict]) -> datetime | None:
    """The earliest moment every window (``next_open`` keyword arguments) is open at once."""
    at = now
    for _ in range(16):
        opens = [next_open(at, **w) for w in windows]
        if None in opens:
            return None
        latest = max(cast(list[datetime], opens))
        if latest == at:
            return at
        at = latest
    return None


def email_windows(campaign: dict, timezone: str) -> list[dict]:
    return [
        {
            'timezone': timezone,
            'start_hour': campaign['send_window_start_hour'],
            'end_hour': campaign['send_window_end_hour'],
            'days': list(campaign['send_window_days']),
        }
    ]


def linkedin_windows(campaign: dict, timezone: str) -> list[dict]:
    return [
        *email_windows(campaign, timezone),
        {'timezone': LINKEDIN_TIMEZONE, 'start_hour': 9, 'end_hour': 18, 'days': [0, 1, 2, 3, 4]},
    ]


def linkedin_replies_today(conn, account_id, now: datetime) -> int:
    """DMs this account sent from the inbox (#187) since midnight in Rome: they share its 30 DMs/day."""
    return conn.execute(
        "SELECT count(*) AS n FROM outreach_inbox_replies WHERE sender_id = %s AND status IN ('sending', 'sent')"
        ' AND created_at >= %s',
        (account_id, day_start(now, LINKEDIN_TIMEZONE)),
    ).fetchone()['n']


def mail_domain(mb: dict) -> str:
    return (
        (mb['account_label'] or '').rpartition('@')[2]
        or settings.outreach_reply_domain
        or urlparse(settings.web_origin).hostname
        or 'zenbree.com'
    )


def reply_to(campaign_id, key: str | None = None) -> str | None:
    """The signed ``Reply-To`` of a send: its campaign's, else (an inbox answer to a message no campaign matched) the
    old form of ``key``. ``None`` until OUTREACH_REPLY_DOMAIN and OUTREACH_REPLY_SECRET are set.

    ponytail: when the old form's parse goes (``reply_targets.in_organization``), that answer needs a reply address
    of its own: with no ``Reply-To`` the person's reply reaches a send-only mailbox and leaves the inbox."""
    domain, secret = settings.outreach_reply_domain, settings.outreach_reply_secret
    if campaign_id:
        return build_campaign_reply_address(campaign_id, domain=domain, secret=secret)
    return build_reply_address(key, domain=domain, secret=secret) if key else None
