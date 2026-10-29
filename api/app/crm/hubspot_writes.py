"""HubSpot CRM writes for write-back (E13.2, #527, #528): contacts, companies, their link, timeline activities and tasks.

Like the reads, every call goes through ``paid_call`` (free, counted) and answers with the error codes of
``app.crm.hubspot_client``. A create that meets an existing record answers ``HUBSPOT.CONFLICT``.
"""

from __future__ import annotations

from app.billing.paid_call import paid_call
from app.core.errors import Result, is_err, ok
from app.crm.hubspot_client import _request

_OBJECTS = '/crm/objects/2026-03'


async def _record_id(method: str, path: str, access_token: str, body: dict) -> Result[str]:
    saved = await _request(method, path, access_token, json=body, record=True)
    return saved if is_err(saved) else ok(str(saved.value.get('id') or ''))


async def create_contact(access_token: str, properties: dict[str, str]) -> Result[str]:
    """The new contact's id. ``HUBSPOT.CONFLICT`` when a contact with that email is already there."""
    return await paid_call(
        'hubspot.write', lambda: _record_id('POST', f'{_OBJECTS}/contacts', access_token, {'properties': properties})
    )


async def update_contact(access_token: str, contact_id: str, properties: dict[str, str]) -> Result[str]:
    path = f'{_OBJECTS}/contacts/{contact_id}'
    return await paid_call('hubspot.write', lambda: _record_id('PATCH', path, access_token, {'properties': properties}))


async def create_company(access_token: str, properties: dict[str, str]) -> Result[str]:
    return await paid_call(
        'hubspot.write', lambda: _record_id('POST', f'{_OBJECTS}/companies', access_token, {'properties': properties})
    )


async def _find_company(access_token: str, domain: str) -> Result[str | None]:
    body = {
        'filterGroups': [{'filters': [{'propertyName': 'domain', 'operator': 'EQ', 'value': domain}]}],
        'properties': ['domain'],
        # The oldest match: the account the team has worked longest, when a portal holds duplicates.
        'sorts': [{'propertyName': 'createdate', 'direction': 'ASCENDING'}],
        'limit': 1,
    }
    found = await _request('POST', f'{_OBJECTS}/companies/search', access_token, json=body)
    if is_err(found):
        return found
    first = found.value['results'][0] if found.value['results'] else None
    return ok(str(first['id']) if isinstance(first, dict) and first.get('id') else None)


async def find_company(access_token: str, domain: str) -> Result[str | None]:
    """The id of the company with that domain, or ``None``."""
    return await paid_call('hubspot.find_company', lambda: _find_company(access_token, domain))


async def associate(access_token: str, contact_id: str, company_id: str) -> Result[str]:
    """Make ``company_id`` a company of the contact, with HubSpot's default association."""
    path = f'/crm/v4/objects/contacts/{contact_id}/associations/default/companies/{company_id}'
    return await paid_call('hubspot.write', lambda: _record_id('PUT', path, access_token, {}))


# HubSpot's own association types from an activity to the record it is about.
_ACTIVITY_TO = {
    'emails': {'contact': 198, 'company': 186},
    'communications': {'contact': 81, 'company': 87},
    'tasks': {'contact': 204, 'company': 192},
    'notes': {'contact': 202, 'company': 190},
}


async def log_activity(
    access_token: str, kind: str, properties: dict[str, str], contact_id: str, company_id: str | None
) -> Result[str]:
    """Put an activity on the contact's timeline, and on its company's: ``kind`` is ``emails``,
    ``communications`` (a LinkedIn message), ``notes`` or ``tasks``. The new activity's id."""
    types = _ACTIVITY_TO[kind]
    to = [('contact', contact_id)] + ([('company', company_id)] if company_id else [])
    body = {
        'properties': properties,
        'associations': [
            {'to': {'id': rid}, 'types': [{'associationCategory': 'HUBSPOT_DEFINED', 'associationTypeId': types[obj]}]}
            for obj, rid in to
        ],
    }
    return await paid_call('hubspot.write', lambda: _record_id('POST', f'{_OBJECTS}/{kind}', access_token, body))
