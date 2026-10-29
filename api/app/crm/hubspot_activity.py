"""The shape of an activity on a HubSpot timeline, shared by the native write-back (``app.crm.activities``, #528) and
the external one (``app.crm.timeline``, #932): the time HubSpot keeps, the headers of an email and the check that an
earlier call landed. No database here: both callers own their own state.
"""

from __future__ import annotations

import json
from datetime import datetime

from app.core.errors import Result, is_err, ok
from app.crm import hubspot_client


def milliseconds(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def headers(email: str, mailbox: str, incoming: bool) -> str:
    """The headers HubSpot shows on an email: who wrote to whom. ``mailbox`` is ours, when known."""
    person = {'email': email}
    ours = [{'email': mailbox}] if '@' in mailbox else []
    if incoming:
        return json.dumps({'from': person, 'to': ours})
    return json.dumps({'from': ours[0], 'to': [person]} if ours else {'to': [person]})


def same_instant(value: object, ms: int) -> bool:
    raw = str(value or '').strip()
    if raw.isdigit():  # epoch milliseconds on older payloads
        return int(raw) == ms
    try:
        return milliseconds(datetime.fromisoformat(raw)) == ms
    except ValueError:
        return False


async def landed(token: str, email: str, ms: int, *, incoming: bool) -> Result[bool]:
    """Whether HubSpot already has an email logged at ``ms``, read from the trace it leaves on the contact.
    ponytail: a later contact logged by someone else in those minutes hides the trace, and the touch is logged
    again; telling the two apart needs the ``sales-email-read`` scope."""
    trace = 'hs_sales_email_last_replied' if incoming else 'notes_last_contacted'
    read = await hubspot_client.read_contacts(token, [email], (trace,))
    if is_err(read):
        return read
    contact = read.value.get(email) or {}
    return ok(same_instant(contact.get(trace), ms))


def refused(error) -> bool:
    """HubSpot refused this activity itself (a 4xx), as opposed to being unreachable, limited or unauthorized."""
    return error.code == 'HUBSPOT.HTTP_ERROR' and 400 <= (error.context.get('status') or 0) < 500
