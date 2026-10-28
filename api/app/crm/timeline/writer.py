"""The drain of the timeline ledger (#932): each due row becomes one activity on the owner's HubSpot contact.

A row is worked as its owner in its organization, oldest first, and the owner's order holds for the sweep: the first
failure of a user's HubSpot leaves their later rows for the next one, because a later activity would hide the trace
that an earlier one is recognised by. An activity HubSpot refuses costs only itself.

What the answer of a call proves decides what happens to the row (see :func:`outcome`): a refusal is counted, a
call HubSpot did not take is released with its request kept, and a call that may have landed stays claimed. The next
sweep asks the contact whether an email landed; a note leaves no trace, so a lost opt-out is ``uncertain``.

ponytail: a lost opt-out is not searched for in the contact's notes. The note ends with a ``zb:<id>`` marker so a
search can resolve it later; it needs a notes read scope nobody has checked on a portal.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from enum import StrEnum
from time import monotonic

import structlog

from app.admin.settings import is_disabled
from app.core import tasks
from app.core.db import get_conn
from app.core.errors import is_err
from app.crm import gate, hubspot_writes
from app.crm.contacts import AccessToken, write_person
from app.crm.contacts import record as record_contact
from app.crm.hubspot_activity import landed, refused
from app.crm.timeline import ledger, queries
from app.crm.timeline.facts import Code, Frozen, Kind, activity_properties, freeze
from app.integrations.providers import HUBSPOT

logger = structlog.get_logger()

BATCH = 40


class Outcome(StrEnum):
    NOT_SENT = 'not_sent'  # HubSpot did not take the request
    REFUSED = 'refused'  # HubSpot took it and said no (a 4xx)
    MAYBE = 'maybe'  # it may have landed: a timeout, a 5xx, a transport error after the send


def outcome(error) -> Outcome:
    """What a failed call proves. ``HUBSPOT.TRANSPORT`` is one code for a refused connection and for a read timeout,
    so it is read as the worse of the two: it may have landed. The activity call tells a 403 from a 401 itself
    (see ``_Drain._settle``); this reads the contact write, where either one stops the owner."""
    if error.code in ('HUBSPOT.RATE_LIMITED', 'HUBSPOT.AUTH'):
        return Outcome.NOT_SENT
    if error.code == 'HUBSPOT.CONFLICT' or refused(error):
        return Outcome.REFUSED
    return Outcome.MAYBE


def _connection(owner_id: str) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM user_oauth_connections WHERE user_id = %s AND provider = 'hubspot' AND status = 'connected'",
            (owner_id,),
        ).fetchone()


class _Drain:
    def __init__(self, access_token: AccessToken, now: datetime) -> None:
        self.access_token = access_token
        self.now = now
        self.counts: Counter = Counter()
        self.stopped: set[str] = set()  # owners whose HubSpot is in trouble: their later rows wait
        self.grants: dict[str, tuple[dict, str] | None] = {}

    async def grant(self, owner: str) -> tuple[dict, str] | None:
        if owner not in self.grants:
            connection = _connection(owner)
            token = await self.access_token(connection, HUBSPOT) if connection else None
            self.grants[owner] = (connection, token) if connection and token else None
            if self.grants[owner] is None:
                self.counts['token_failed'] += 1
        return self.grants[owner]

    async def work(self, row: dict) -> None:
        owner = str(row['owner_id'])
        if row['outside']:
            ledger.skip(row, Code.OUTSIDE_WRITE_BACK)
            self.counts['skipped'] += 1
        elif row['native_duplicate']:
            ledger.skip(row, Code.NATIVE_DUPLICATE)
            self.counts['skipped'] += 1
        elif row['too_soon']:
            self.stopped.add(owner)  # HubSpot takes seconds to put a new activity's time on its contact
        else:
            await self._log(row, owner)

    async def _log(self, row: dict, owner: str) -> None:
        granted = await self.grant(owner)
        if granted is None:
            self.stopped.add(owner)
            return
        connection, token = granted
        if row['current_portal'] != connection['provider_account_id']:
            self.stopped.add(owner)  # reconnected to another portal since the sweep read it
            return
        if row['state'] == 'claimed' and await self._after_lost_answer(row, owner, token, connection):
            return
        written = await self._contact(row, owner, token, connection)
        if written is None:
            return
        claimed = ledger.claim(row, freeze(Kind(row['kind']), row['occurred_at']), connection['provider_account_id'])
        if claimed is None:
            self.counts['busy'] += 1  # another sweep has it
            return
        frozen = Frozen.of(claimed)
        props = activity_properties(row, frozen, connection.get('provider_owner_id'))
        company_id = written[1] if row['companies'] else None
        logged = await hubspot_writes.log_activity(token, frozen.object, props, written[0], company_id)
        self._settle(row, owner, logged)

    async def _after_lost_answer(self, row: dict, owner: str, token: str, connection: dict) -> bool:
        """A claimed row seen again: the earlier call may have landed. True when the row is settled or the owner
        stopped; False when the request is to be sent again."""
        if row['portal_id'] != connection['provider_account_id']:
            ledger.release(row)  # the claim went to another portal: the row is outside the window by now
            return True
        frozen = Frozen.of(row['request'])
        if frozen.object == 'notes':
            ledger.settle_uncertain(row)
            self.counts['uncertain'] += 1
            return True
        seen = await landed(token, row['email'], frozen.hs_timestamp, incoming=frozen.direction == 'INCOMING_EMAIL')
        if is_err(seen):
            self.stopped.add(owner)
            self.counts['failed'] += 1
            return True
        if seen.value:
            ledger.settle_logged(row, '')
            self.counts['logged'] += 1
        return seen.value

    async def _contact(self, row: dict, owner: str, token: str, connection: dict) -> tuple[str, str | None] | None:
        """The contact to log on, written first when there is none. ``None`` when the row waits or failed."""
        if row['contact_id']:
            return row['contact_id'], row['company_id']
        if not row['gated']:
            try:
                verdicts = await gate.evaluate(connection, token, [row['email']], self.now)
            except gate.ReadFailed as failure:
                if failure.permanent:
                    gate.mark_error(connection, failure.code)
                self.stopped.add(owner)
                self.counts['failed'] += 1
                return None
            reason, hold_until = verdicts[0][1], verdicts[0][2]
            if reason and (hold_until is None or hold_until > self.now):
                self.counts['held'] += 1
                return None
        person = {
            'owner_id': owner,
            'email': row['email'],
            'first_name': row['person'].get('first_name'),
            'last_name': row['person'].get('last_name'),
            'role': row['person'].get('title'),
            'company_name': row['person'].get('company_name'),
            'companies': row['companies'],
        }
        written = await write_person(token, person)
        if is_err(written):
            self._settle_contact(row, owner, written.error)
            return None
        record_contact(owner, row['email'], written.value, connection['provider_account_id'])
        return written.value[0], written.value[1]

    def _settle_contact(self, row: dict, owner: str, error) -> None:
        logger.warning('crm.timeline.contact_failed', user_id=owner, error_code=error.code)
        if outcome(error) is Outcome.REFUSED:
            ledger.settle_refused(row, Code.CONTACT_REFUSED)
            self.counts['refused'] += 1
        else:
            self.stopped.add(owner)
            self.counts['failed'] += 1

    def _settle(self, row: dict, owner: str, logged) -> None:
        if not is_err(logged):
            ledger.settle_logged(row, logged.value)
            self.counts['logged'] += 1
            return
        logger.warning('crm.timeline.log_failed', user_id=owner, kind=row['kind'], error_code=logged.error.code)
        if logged.error.code == 'HUBSPOT.AUTH':
            if logged.error.context.get('status') == 403:
                # A 403 proves HubSpot took nothing and the grant works for other calls: a missing scope for this
                # object. Counted, it fails visibly after MAX_ATTEMPTS; the owner's later rows still go this sweep.
                ledger.settle_refused(row, Code.REFUSED)
                self.counts['refused'] += 1
            else:
                ledger.release(row)  # a 401 is the token: nothing counted against the row, the owner waits
                self.stopped.add(owner)
                self.counts['failed'] += 1
            return
        if logged.error.code == 'HUBSPOT.INVALID_JSON':
            # A 2xx whose body is unreadable: HubSpot took the activity and only its id is lost.
            ledger.settle_logged(row, '')
            self.counts['logged'] += 1
            return
        match outcome(logged.error):
            case Outcome.REFUSED:
                ledger.settle_refused(row, Code.REFUSED)
                self.counts['refused'] += 1
            case Outcome.NOT_SENT:
                ledger.release(row)  # a 429 never turns an opt-out uncertain
                self.stopped.add(owner)
                self.counts['failed'] += 1
            case Outcome.MAYBE:
                self.stopped.add(owner)  # stays claimed: the next sweep asks the contact
                self.counts['failed'] += 1


async def drain(access_token: AccessToken, deadline: float) -> Counter:
    """Log every due row until ``deadline`` (monotonic). Runs inside the beat's System task."""
    # Before the kill switch: settling orphans calls no HubSpot, and a switch left on must not keep a dead Retry.
    ledger.settle_orphans()
    if is_disabled('vendor_disabled:hubspot'):
        return Counter()
    run = _Drain(access_token, datetime.now(UTC))
    for row in queries.due(run.now, BATCH):
        owner = str(row['owner_id'])
        if monotonic() > deadline:
            run.counts['cut'] += 1
            break
        if owner in run.stopped:
            continue
        # As the owner, in the row's organization: an owner who left it gets nothing more logged from it.
        with tasks.as_member(str(row['organization_id']), owner):
            await run.work(row)
    return run.counts
