"""HubSpot webhook recheck (E13.1 #120): re-reads the people whose stored verdict rests on a changed record."""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from app.admin.settings import is_disabled
from app.core import tasks
from app.core.db import get_conn
from app.crm import hubspot_client
from app.crm.gate import AccessToken, ReadFailed, evaluate, mark_error, token_unavailable, unwrap
from app.integrations.providers import HUBSPOT


def _portal_connections(portal_ids: list[str]) -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM user_oauth_connections WHERE provider = 'hubspot' AND status = 'connected'"
            ' AND user_id IS NOT NULL AND provider_account_id = ANY(%s)',
            (portal_ids,),
        ).fetchall()


def _known_emails(owner_id: str, contact_ids: list[str], company_ids: list[str]) -> list[str]:
    """The people of this owner whose stored verdict rests on one of those HubSpot records."""
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT email FROM crm_gate_checks WHERE owner_id = %s AND (contact_id = ANY(%s) OR company_id = ANY(%s))',
            (owner_id, contact_ids, company_ids),
        ).fetchall()
    return [r['email'] for r in rows]


def _has_verdicts(owner_id: str) -> bool:
    with get_conn() as conn:
        return bool(conn.execute('SELECT 1 FROM crm_gate_checks WHERE owner_id = %s LIMIT 1', (owner_id,)).fetchone())


async def _recheck_owner(
    connection: dict, events: list[dict], access_token: AccessToken, now: datetime, counts: Counter
) -> None:
    owner = str(connection['user_id'])
    if not _has_verdicts(owner):
        return  # nobody of this owner was ever checked: nothing a change could affect
    ids: dict[str, list[str]] = {'contact': [], 'company': [], 'deal': []}
    for event in events:
        if event['id'] not in ids[event['kind']]:
            ids[event['kind']].append(event['id'])
    token: str | None = None
    if ids['deal']:
        token = await access_token(connection, HUBSPOT)
        if token is None:
            return token_unavailable(connection, counts)
        for start in range(0, len(ids['deal']), hubspot_client.BATCH_SIZE):
            deals = ids['deal'][start : start + hubspot_client.BATCH_SIZE]
            companies = unwrap(await hubspot_client.deal_companies(token, deals))
            ids['company'].extend(c for c in companies if c not in ids['company'])
    emails = _known_emails(owner, ids['contact'], ids['company'])
    if not emails:
        return
    token = token or await access_token(connection, HUBSPOT)
    if token is None:
        return token_unavailable(connection, counts)
    for start in range(0, len(emails), hubspot_client.BATCH_SIZE):
        batch = emails[start : start + hubspot_client.BATCH_SIZE]
        await evaluate(connection, token, batch, now)
        counts['rechecked'] += len(batch)


async def recheck(events: list[dict], now: datetime, access_token: AccessToken) -> Counter:
    """A HubSpot webhook delivery: ``events`` are ``{'portal', 'kind': contact|company|deal, 'id'}``.
    Re-reads every person whose verdict rests on a changed record and stores the new verdict, for
    each user connected to that portal. Raises :class:`ReadFailed` when HubSpot cannot be read for a
    transient reason, so the delivery is retried rather than an unblock (or a new block) lost; a
    refused grant marks that connection ``error`` and is not retried."""
    counts: Counter = Counter()
    if not events:
        return counts
    if is_disabled('vendor_disabled:hubspot'):
        raise ReadFailed('HUBSPOT.DISABLED')
    for connection in _portal_connections(sorted({e['portal'] for e in events})):
        mine = [e for e in events if e['portal'] == connection['provider_account_id']]
        # The verdicts are the owner's in every organization: run as the owner, in the organization that holds the
        # grant. Removing a member there disconnects it, so a connected grant's owner is a member there, or banned.
        with tasks.as_member(str(connection['organization_id']), str(connection['user_id'])):
            try:
                await _recheck_owner(connection, mine, access_token, now, counts)
            except ReadFailed as failure:
                if not failure.permanent:
                    raise
                mark_error(connection, failure.code)
                counts['auth_failed'] += 1
    return counts
