"""HubSpot CRM reads for the read gate (#120), and the request helper the writes share
(``app.crm.hubspot_writes``, #527). Plain httpx, no vendor SDK; date-versioned endpoints.

Every call goes through :func:`app.billing.paid_call.paid_call`: HubSpot API calls are free, but they
are counted on /admin/costs like any vendor call.

Error codes:
- HUBSPOT.AUTH          401/403 (revoked grant, missing scope)
- HUBSPOT.RATE_LIMITED  429
- HUBSPOT.CONFLICT      409 (a create met a record that already exists)
- HUBSPOT.TRANSPORT     network failure / timeout
- HUBSPOT.HTTP_ERROR    any other non-2xx
- HUBSPOT.INVALID_JSON  2xx body we cannot read
"""

from __future__ import annotations

import httpx
import structlog

from app.billing.paid_call import paid_call
from app.core.errors import Result, err, is_err, ok

logger = structlog.get_logger()

BASE_URL = 'https://api.hubapi.com'
BATCH_SIZE = 100  # HubSpot's batch read limit
# associatedcompanyid: the contact's primary company. hs_email_optout: unsubscribed from all email.
CONTACT_PROPERTIES = (
    'email',
    'notes_last_contacted',
    'hs_email_optout',
    'hubspot_owner_id',
    'associatedcompanyid',
)
# hs_num_open_deals: deals of the company not in a closed-won or closed-lost stage.
COMPANY_PROPERTIES = ('lifecyclestage', 'hubspot_owner_id', 'hs_num_open_deals')
_TIMEOUT_SECONDS = 15.0


async def _request(
    method: str,
    path: str,
    access_token: str,
    *,
    json: dict | None = None,
    params: dict | None = None,
    record: bool = False,
) -> Result[dict]:
    """``record``: the answer is one object (a create, an update, an association), not a ``results`` list."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.request(
                method,
                f'{BASE_URL}{path}',
                headers={'Authorization': f'Bearer {access_token}'},
                json=json,
                params=params,
            )
    except httpx.HTTPError as exc:
        logger.warning('hubspot.transport_error', error=repr(exc)[:200])
        return err('HUBSPOT.TRANSPORT', detail='HubSpot unreachable')
    if resp.status_code in (401, 403):
        return err('HUBSPOT.AUTH', detail='HubSpot refused the grant', status=resp.status_code)
    if resp.status_code == 409:
        return err('HUBSPOT.CONFLICT', detail='the HubSpot record already exists')
    if resp.status_code == 429:
        wait = resp.headers.get('Retry-After', '')
        # Seconds, as HubSpot sends it; the caller waits at least that long before the next read.
        return err(
            'HUBSPOT.RATE_LIMITED', detail='HubSpot rate limited', retry_after=int(wait) if wait.isdigit() else None
        )
    # 207 on a batch read: some inputs have no record; they are simply absent from ``results``.
    if resp.status_code not in (200, 201, 207):
        return err('HUBSPOT.HTTP_ERROR', detail=f'HubSpot returned {resp.status_code}', status=resp.status_code)
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict) or (not record and not isinstance(payload.get('results'), list)):
        return err('HUBSPOT.INVALID_JSON', detail='HubSpot answer has no results')
    return ok(payload)


def _records(payload: dict) -> list[tuple[str, dict]]:
    """``(record id, properties)`` of every well-formed record."""
    out = []
    for record in payload['results']:
        props = record.get('properties') if isinstance(record, dict) else None
        if isinstance(props, dict) and record.get('id'):
            out.append((str(record['id']), props))
    return out


async def _read_contacts(
    access_token: str, emails: list[str], properties: tuple[str, ...] = CONTACT_PROPERTIES
) -> Result[dict[str, dict]]:
    body = {'idProperty': 'email', 'inputs': [{'id': e} for e in emails], 'properties': list(properties)}
    payload = await _request('POST', '/crm/objects/2026-03/contacts/batch/read', access_token, json=body)
    if is_err(payload):
        return payload
    found: dict[str, dict] = {}
    for record_id, props in _records(payload.value):
        email = props.get('email')
        if isinstance(email, str) and email.strip():
            found[email.strip().lower()] = {**props, 'id': record_id}
    return ok(found)


async def read_contacts(
    access_token: str, emails: list[str], properties: tuple[str, ...] = CONTACT_PROPERTIES
) -> Result[dict[str, dict]]:
    """Contact properties (plus the record ``id``) by lower-cased email, for at most
    :data:`BATCH_SIZE` emails. An email with no HubSpot contact is absent from the result."""
    if len(emails) > BATCH_SIZE:
        raise ValueError(f'at most {BATCH_SIZE} emails per batch read')
    return await paid_call(
        'hubspot.read_contacts', lambda: _read_contacts(access_token, emails, properties), count=len(emails)
    )


async def _read_companies(access_token: str, ids: list[str]) -> Result[dict[str, dict]]:
    body = {'inputs': [{'id': i} for i in ids], 'properties': list(COMPANY_PROPERTIES)}
    payload = await _request('POST', '/crm/objects/2026-03/companies/batch/read', access_token, json=body)
    if is_err(payload):
        return payload
    return ok(dict(_records(payload.value)))


async def read_companies(access_token: str, ids: list[str]) -> Result[dict[str, dict]]:
    """Company properties by record id, for at most :data:`BATCH_SIZE` ids."""
    if len(ids) > BATCH_SIZE:
        raise ValueError(f'at most {BATCH_SIZE} companies per batch read')
    return await paid_call('hubspot.read_companies', lambda: _read_companies(access_token, ids), count=len(ids))


async def _find_owner(access_token: str, email: str) -> Result[str | None]:
    payload = await _request('GET', '/crm/owners/2026-03', access_token, params={'email': email, 'limit': 1})
    if is_err(payload):
        return payload
    for owner in payload.value['results']:
        if isinstance(owner, dict) and owner.get('id'):
            return ok(str(owner['id']))
    return ok(None)


async def find_owner(access_token: str, email: str) -> Result[str | None]:
    """The HubSpot owner id of the user with that email, or ``None`` when they own nothing."""
    return await paid_call('hubspot.find_owner', lambda: _find_owner(access_token, email))


async def _deal_companies(access_token: str, deal_ids: list[str]) -> Result[list[str]]:
    body = {'inputs': [{'id': i} for i in deal_ids]}
    payload = await _request('POST', '/crm/v4/associations/deals/companies/batch/read', access_token, json=body)
    if is_err(payload):
        return payload
    found: list[str] = []
    for record in payload.value['results']:
        for to in record.get('to') or [] if isinstance(record, dict) else []:
            company = to.get('toObjectId') if isinstance(to, dict) else None
            if company and str(company) not in found:
                found.append(str(company))
    return ok(found)


async def deal_companies(access_token: str, deal_ids: list[str]) -> Result[list[str]]:
    """Ids of the companies associated with those deals (at most :data:`BATCH_SIZE` deals)."""
    if len(deal_ids) > BATCH_SIZE:
        raise ValueError(f'at most {BATCH_SIZE} deals per association read')
    return await paid_call(
        'hubspot.deal_companies', lambda: _deal_companies(access_token, deal_ids), count=len(deal_ids)
    )
