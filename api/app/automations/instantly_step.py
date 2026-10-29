"""The automation step that hands a run's people to a campaign of the project's Instantly workspace (#1032).

The step makes no batch and writes nothing. Its fetch reads the campaign from Instantly; its apply judges the people
with the #929 rule (``instantly_verdicts.judge``, database only) and keeps the ones who would go on the run. The run's
``finish`` holds them when the run succeeds, and ``instantly_handoff`` turns the hold into the batch. The preview reads
the campaign the same way.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from typing import TypeIs

from app.admin.settings import is_disabled
from app.automations.models import AddToInstantly, Definition
from app.automations.run import Handoff, Outcome, Run, StepFailed
from app.billing import row_charges
from app.core.db import get_conn
from app.core.errors import Result, err, is_err, ok
from app.integrations import instantly_client, instantly_enroll, instantly_store, instantly_verdicts
from app.integrations.instantly_client import Campaign


@dataclass(frozen=True, slots=True)
class Reading:
    campaign: Campaign
    workspace_id: str  # the workspace whose key read it: the batch must go to the same one


def connected(connection: dict | None) -> TypeIs[dict]:
    """The project's connection can call Instantly: what ``instantly_store.api_key`` needs, read without the key."""
    return bool(connection and connection['status'] == 'connected' and connection['api_key_encrypted'])


def snapshot(campaign: Campaign) -> dict:
    return {'id': campaign.id, 'name': campaign.name, 'status': campaign.status, 'may_send': campaign.may_send}


async def read_campaign(project_id: str, campaign_id: str) -> Result[Reading]:
    """The campaign as Instantly has it now, through the project's connection. One read, no write."""
    if is_disabled('vendor_disabled:instantly'):
        return err('INSTANTLY.DISABLED', detail='Instantly is switched off for now')
    connection = await asyncio.to_thread(_connection, project_id)
    key = instantly_store.api_key(connection)
    if is_err(key):
        return key
    if connection is None:  # api_key refuses a missing connection: this only tells the type checker
        return err('INSTANTLY.NOT_CONNECTED', detail='connect Instantly first')
    found = await instantly_client.get_campaign(key.value, campaign_id)
    if is_err(found):
        return found
    return ok(Reading(found.value, connection['workspace_id']))


def _connection(project_id: str) -> dict | None:
    with get_conn() as conn:
        return instantly_store.row(conn, project_id)


async def preview(shown: dict, project_id: str, definition: Definition) -> dict:
    """The preview's ``instantly``: the step's campaign, read once. A campaign Instantly does not have is a problem;
    an Instantly that does not answer is none, since the run reads the campaign again."""
    step = definition.step('add_to_instantly')
    if not isinstance(step, AddToInstantly):
        return shown
    out: dict = {'connected': True, 'campaign': None, 'unreadable': None}
    if any(p['code'] == 'AUTOMATION.INSTANTLY_NOT_CONNECTED' for p in shown['problems']):
        return {**shown, 'instantly': {**out, 'connected': False}}
    read = await read_campaign(project_id, str(step.campaign_id))
    if is_err(read) and read.error.code == 'INSTANTLY.NOT_FOUND':
        gone = {
            'code': 'AUTOMATION.INSTANTLY_CAMPAIGN_NOT_FOUND',
            'message': 'pick a campaign of the Instantly workspace',
        }
        return {**shown, 'problems': [*shown['problems'], gone], 'instantly': out}
    if is_err(read):
        return {**shown, 'instantly': {**out, 'unreadable': read.error.code}}
    campaign = read.value.campaign
    shown_campaign = {**snapshot(campaign), 'url': instantly_enroll.campaign_url(campaign.id)}
    return {**shown, 'instantly': {**out, 'campaign': shown_campaign}}


# A read that fails for one of these fails the same way when tried again: the run stops at once.
_PERMANENT = {
    'INSTANTLY.DISABLED': 'vendor_disabled',
    'INSTANTLY.NOT_CONNECTED': 'instantly_not_connected',
    # The stored key cannot be decrypted (``token_crypto.decrypt``), so for the run the project has no key.
    'INTEGRATIONS.CRYPTO_FAILED': 'instantly_not_connected',
    'INTEGRATIONS.NOT_CONFIGURED': 'instantly_not_connected',
    'INSTANTLY.NOT_FOUND': 'instantly_campaign_not_found',
    'INSTANTLY.INVALID_KEY': 'instantly_refused',
    'INSTANTLY.PLAN_REQUIRED': 'instantly_refused',
    'INSTANTLY.MISSING_SCOPE': 'instantly_refused',
    'INSTANTLY.REJECTED': 'instantly_refused',
}


async def fetch_instantly(run: Run, step: AddToInstantly, position: int) -> Reading:
    read = await read_campaign(run.project_id, str(step.campaign_id))
    if is_err(read):
        code = read.error.code
        failure = _PERMANENT.get(code)
        raise StepFailed(failure or 'instantly_unreachable', retry=failure is None, output={'instantly_error': code})
    return read.value


def apply_instantly(conn, tab, run: Run, step: AddToInstantly, position: int, read: Reading) -> Outcome:
    """An estimate of what the batch will do: it judges every person again, under the project lock, when it is made."""
    if run.hold_id:
        # ``load_rows`` hides what a reserved hold bought, the email of an earlier step included.
        row_charges.finalize_hold(conn, run.hold_id)
    rows = instantly_enroll.load_rows(conn, run.list_id, run.row_ids)
    verdicts = instantly_verdicts.judge(conn, project_id=run.project_id, owner_id=run.user_id, rows=rows)
    going = [v.row_id for v in verdicts if not v.reason]
    run.handoff = Handoff(going, read.workspace_id, read.campaign)
    when = 'once the run is approved' if run.review else 'when the run ends'
    return Outcome(
        f'{len(going)} of {len(verdicts)} people go to Instantly {when}',
        {
            'held': len(going),
            'eligible': len(going),
            'skipped': dict(Counter(v.reason for v in verdicts if v.reason)),
            'campaign': snapshot(read.campaign),
        },
    )


# The judge's reasons in the words ``gate.take`` already counts a native refusal with.
_REFUSED_AS = {
    'excluded': 'excluded',
    'replied_or_contacted': 'already_handled',
    'in_native_campaign': 'in_other_campaign',
    'already_in_instantly': 'held_by_instantly',
    'stopped': 'stopped',
}


def free_refusals(conn, run: Run, rows: list[dict]) -> dict[str, str]:
    """``{row id: reason}`` of the people ``gate.take`` keeps that the batch would refuse for good, so no credit is
    spent on them. A person with no email yet, or with the email of another person here, is left to the batch."""
    if not isinstance(run.definition.step('add_to_instantly'), AddToInstantly):
        return {}
    verdicts = instantly_verdicts.judge(
        conn, project_id=run.project_id, owner_id=run.user_id, rows=rows, check_crm=False
    )
    return {v.row_id: _REFUSED_AS[v.reason] for v in verdicts if v.reason in _REFUSED_AS}
