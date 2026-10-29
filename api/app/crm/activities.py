"""Write-back of touches to HubSpot (E13.2, #528): each delivered send and each reply, on the contact's timeline.

A beat sweep like ``app.crm.contacts``, which it follows: a send or a reply never waits on HubSpot, and a touch is
logged only for a person whose contact that sweep has written. An email is a HubSpot email activity, a LinkedIn
message a HubSpot "LinkedIn message" (a communication), so both count in the portal's activity reports.

Both also move the contact's "Last contacted", which the read gate holds people on for 30 days. So the row in
``crm_activity_writes`` is written before HubSpot is called: ``app.crm.gate`` reads it and never takes a touch
Zenbree logged for somebody else's.

The grant cannot read emails back (HubSpot wants ``sales-email-read`` for that, seen on a real portal), so after a
call whose answer was lost the contact itself is asked: HubSpot sets its "Last contacted", or "Recent sales email
replied date" for an incoming email, to the exact time we sent with the activity.

Not logged: automatic replies (out of office), the answers an owner sends from the inbox, and anything from before
write-back was turned on.

This ledger keeps the two sources of #528, campaign sends and inbound messages. Every later source (an external
sequencer, #932) uses ``app.crm.timeline.record``, which has its own states and status.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

import structlog

from app.admin.settings import is_disabled
from app.core import tasks
from app.core.db import get_conn
from app.core.errors import Result, is_err, ok
from app.crm import hubspot_writes
from app.crm.contacts import AccessToken
from app.crm.hubspot_activity import headers, landed, milliseconds, refused
from app.integrations.providers import HUBSPOT, HUBSPOT_COMPANIES_WRITE, HUBSPOT_CONTACTS_WRITE

logger = structlog.get_logger()

# Activities per sweep, over all users: one HubSpot call each, two after a lost answer.
# ``activity_id`` of an activity HubSpot has but whose id went with the lost answer.
ID_LOST = ''
BATCH = 80
# Refusals of one activity (a contact deleted in HubSpot, a body HubSpot will not take) before it is given up, so
# it cannot hold the queue of everything behind it.
MAX_ATTEMPTS = 5

_CONNECTED = """h.provider = 'hubspot' AND h.status = 'connected' AND h.crm_write_back
  AND %(contacts)s = ANY(h.granted_scopes)"""
_NOT_LOGGED = '(a.source_id IS NULL OR (a.activity_id IS NULL AND a.attempts < %(max)s))'
# HubSpot takes seconds to put a new activity's time on its contact, where a retry looks for it.
_TOO_SOON = "coalesce(a.attempted_at > now() - interval '1 minute', false)"
# Sends and replies since write-back went on, of people with a written contact, not yet on the timeline; oldest
# first, so a timeline fills in order. ``retried``: an earlier call for it may have reached HubSpot. Only those of an
# organization the owner is a live member of: each is logged as Member(that organization, owner).
_LIVE = '(h.user_id, {}.organization_id) IN (SELECT user_id, organization_id FROM zb.live_members)'
# The same email already logged, or being logged, by the external timeline (#932): a mailbox connected both here and
# to an external sequencer sees one reply twice. Both ledgers compare the Message-ID without brackets, lowercase, and
# an empty one never matches.
_TIMELINE_HAS = """EXISTS (SELECT 1 FROM crm_timeline_events x
  WHERE x.owner_id = h.user_id AND x.kind = 'incoming_email' AND x.state IN ('claimed', 'logged', 'uncertain')
    AND x.message_id = nullif(lower(btrim(m.rfc_message_id, '<> ')), ''))"""
_DUE_SQL = f"""
SELECT 'send' AS kind, s.id AS source_id, h.user_id AS owner_id, s.organization_id, w.email, w.contact_id, w.company_id,
       s.channel, s.subject, coalesce(s.body, '') AS body, s.delivered_at AS touched_at, c.name AS campaign,
       'reply' AS inbound_kind, '' AS from_name, coalesce(mb.account_label, '') AS mailbox,
       a.source_id IS NOT NULL AS retried, {_TOO_SOON} AS too_soon,
       %(companies)s = ANY(h.granted_scopes) AS companies, h.provider_account_id AS portal_id
FROM user_oauth_connections h
JOIN outreach_campaign_sends s
  ON s.owner_id = h.user_id AND s.status = 'sent' AND s.delivered_at >= h.crm_write_back_since
JOIN outreach_campaign_targets t ON t.id = s.target_id
JOIN crm_contact_writes w ON w.owner_id = h.user_id AND w.email = lower(btrim(t.email))
JOIN outreach_campaigns c ON c.id = s.campaign_id
LEFT JOIN user_oauth_connections mb ON mb.id = s.sender_id
LEFT JOIN crm_activity_writes a ON a.kind = 'send' AND a.source_id = s.id
WHERE {_CONNECTED} AND {_NOT_LOGGED} AND {_LIVE.format('s')}
UNION ALL
SELECT 'reply', m.id, h.user_id, m.organization_id, w.email, w.contact_id, w.company_id,
       CASE m.channel WHEN 'email' THEN 'email' ELSE 'linkedin_message' END, m.subject, m.body, m.received_at,
       coalesce(c.name, ''), m.kind, m.from_name, '',
       a.source_id IS NOT NULL, {_TOO_SOON}, %(companies)s = ANY(h.granted_scopes), h.provider_account_id
FROM user_oauth_connections h
JOIN outreach_inbound_messages m
  ON m.owner_id = h.user_id AND m.kind IN ('reply', 'unsubscribe') AND m.received_at >= h.crm_write_back_since
LEFT JOIN outreach_campaign_targets t ON t.id = m.target_id
JOIN crm_contact_writes w
  ON w.owner_id = h.user_id AND w.email = lower(btrim(coalesce(nullif(btrim(t.email), ''), m.from_address)))
LEFT JOIN outreach_campaigns c ON c.id = m.campaign_id
LEFT JOIN crm_activity_writes a ON a.kind = 'reply' AND a.source_id = m.id
WHERE {_CONNECTED} AND {_NOT_LOGGED} AND {_LIVE.format('m')} AND NOT {_TIMELINE_HAS}
ORDER BY touched_at
LIMIT %(batch)s"""


def _note(touch: dict) -> str:
    """The line under the message that says where it comes from: HubSpot has no field for the campaign."""
    parts = ['Zenbree', touch['campaign']]
    if touch['channel'] == 'linkedin_connect':
        parts.append('LinkedIn invitation')
    if touch['inbound_kind'] == 'unsubscribe':
        parts.append('unsubscribed')
    return '— ' + ' · '.join(p for p in parts if p)


def activity(touch: dict, owner_id: str | None) -> tuple[str, dict[str, str]]:
    """``(HubSpot object, properties)`` for one send or reply. ``owner_id``: the connected user as a HubSpot
    owner, shown as who logged it."""
    incoming = touch['kind'] == 'reply'
    text = '\n\n'.join(p for p in (touch['body'].strip(), _note(touch)) if p)
    props = {'hs_timestamp': str(milliseconds(touch['touched_at']))}
    if owner_id:
        props['hubspot_owner_id'] = owner_id
    if touch['channel'] != 'email':
        who = touch['from_name'].strip() or touch['email']
        return 'communications', {
            **props,
            'hs_communication_channel_type': 'LINKEDIN_MESSAGE',
            'hs_communication_logged_from': 'CRM',
            # A communication has no direction: an answer says whose it is.
            'hs_communication_body': f'{who}:\n{text}' if incoming else text,
        }
    return 'emails', {
        **props,
        'hs_email_direction': 'INCOMING_EMAIL' if incoming else 'EMAIL',
        'hs_email_status': 'SENT',
        'hs_email_subject': touch['subject'],
        'hs_email_text': text,
        'hs_email_headers': headers(touch['email'], touch['mailbox'], incoming),
    }


def _due() -> list[dict]:
    params = {
        'contacts': HUBSPOT_CONTACTS_WRITE,
        'companies': HUBSPOT_COMPANIES_WRITE,
        'max': MAX_ATTEMPTS,
        'batch': BATCH,
    }
    with get_conn() as conn:
        return conn.execute(_DUE_SQL, params).fetchall()


def _connection(owner_id: str) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM user_oauth_connections WHERE user_id = %s AND provider = 'hubspot' AND status = 'connected'",
            (owner_id,),
        ).fetchone()


def own_touches(owner_id: str, emails: list[str]) -> dict[str, datetime]:
    """The latest touch Zenbree logged, or is logging, for each of these people of ``owner_id``: a native send or
    reply, and an email an external sequencer sent (#932). An opt-out note is not a contact, so HubSpot does not move
    "Last contacted" for it and it is not counted."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT email, max(at) AS at FROM (
                 SELECT email, touched_at AS at FROM crm_activity_writes WHERE owner_id = %(owner)s
                 UNION ALL
                 SELECT email, to_timestamp((request->>'hs_timestamp')::bigint / 1000.0) FROM crm_timeline_events
                 WHERE owner_id = %(owner)s AND kind <> 'opt_out' AND state IN ('claimed', 'logged', 'uncertain')
               ) t WHERE email = ANY(%(emails)s) GROUP BY email""",
            {'owner': owner_id, 'emails': emails},
        ).fetchall()
    return {r['email']: r['at'] for r in rows}


def _claim(touch: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            'INSERT INTO crm_activity_writes (kind, source_id, owner_id, email, touched_at) VALUES (%s, %s, %s, %s, %s)'
            ' ON CONFLICT (kind, source_id) DO UPDATE SET attempted_at = now()',
            (touch['kind'], touch['source_id'], touch['owner_id'], touch['email'], touch['touched_at']),
        )


def _settle(touch: dict, activity_id: str | None) -> None:
    """The activity's id, or one more refusal when HubSpot would not take it."""
    with get_conn() as conn:
        conn.execute(
            'UPDATE crm_activity_writes SET activity_id = %s, attempts = attempts + %s WHERE kind = %s AND source_id = %s',
            (activity_id, 1 if activity_id is None else 0, touch['kind'], touch['source_id']),
        )


async def log(token: str, touch: dict, owner_id: str | None) -> Result[str]:
    """Log one touch, once: after an earlier call whose answer was lost, HubSpot is asked first."""
    kind, properties = activity(touch, owner_id)
    if touch['retried']:
        incoming = kind == 'emails' and properties['hs_email_direction'] == 'INCOMING_EMAIL'
        seen = await landed(token, touch['email'], int(properties['hs_timestamp']), incoming=incoming)
        if is_err(seen):
            return seen
        if seen.value:
            return ok(ID_LOST)
    _claim(touch)
    company_id = touch['company_id'] if touch['companies'] else None
    return await hubspot_writes.log_activity(token, kind, properties, touch['contact_id'], company_id)


async def sweep(access_token: AccessToken) -> Counter:
    """Log every touch that is due. As in the contacts sweep, a user whose HubSpot is in trouble is left for the
    next sweep at the first failure, so their touches are logged oldest first; an activity HubSpot refuses costs
    only itself."""
    counts: Counter = Counter()
    if is_disabled('vendor_disabled:hubspot'):
        return counts
    tokens: dict[str, tuple[str, str | None, str | None] | None] = {}
    for touch in _due():
        # As the owner, in the touch's organization: an owner who left it gets nothing more logged from it.
        with tasks.as_member(str(touch['organization_id']), str(touch['owner_id'])):
            await _log_one(touch, tokens, access_token, counts)
    return counts


async def _log_one(
    touch: dict,
    tokens: dict[str, tuple[str, str | None, str | None] | None],
    access_token: AccessToken,
    counts: Counter,
) -> None:
    owner = str(touch['owner_id'])
    if touch['too_soon']:
        # Everything of this user waits with it: touches go to HubSpot in order, because a later one of the
        # same person would hide the trace this one is recognised by.
        tokens[owner] = None
        return
    if owner not in tokens:
        connection = _connection(owner)
        token = await access_token(connection, HUBSPOT) if connection else None
        tokens[owner] = (
            (token, connection.get('provider_owner_id'), connection['provider_account_id'])
            if connection and token
            else None
        )
        if tokens[owner] is None:
            counts['token_failed'] += 1
    if (granted := tokens[owner]) is None:
        return
    if touch['portal_id'] != granted[2]:
        return  # reconnected to another portal since the sweep read it: this touch belongs to the old one
    logged = await log(granted[0], touch, granted[1])
    if is_err(logged):
        logger.warning('crm.activities.log_failed', user_id=owner, kind=touch['kind'], error_code=logged.error.code)
        if refused(logged.error):
            _settle(touch, None)
            counts['refused'] += 1
        else:
            tokens[owner] = None
            counts['failed'] += 1
        return
    _settle(touch, logged.value)  # ``ID_LOST`` settles it too
    counts['sent' if touch['kind'] == 'send' else 'replies'] += 1
