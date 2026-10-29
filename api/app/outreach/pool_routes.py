"""The mailbox accounts a campaign sends from (#935): ``GET`` and ``PUT /v1/campaigns/{id}/senders``. The pool is
set by account id, from the Google and Microsoft accounts linked to the campaign's client. Instantly keeps its own
accounts and never appears here."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.middleware import CurrentUser, current_user
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.integrations.sender_state import Hold, Waiting, mailbox_kill_switches
from app.outreach import pool_state, sender_pool, sending
from app.outreach.pool_state import Blocker, PoolAccount
from app.outreach.routes import _own

router = APIRouter(prefix='/v1/campaigns')
Auth = Depends(current_user)


class CampaignSender(BaseModel):
    id: UUID
    provider: Literal['google', 'microsoft']
    account_label: str | None
    status: Literal['connected', 'error', 'disconnected']
    picked: bool
    state: Literal['ready', 'waiting', 'held']
    reason: Hold | Waiting | None
    blocker: Blocker | None
    resumes_at: datetime | None
    sent_today: int
    daily_cap: int
    warming_up: bool


class CampaignSenders(BaseModel):
    senders: list[CampaignSender]
    max_senders: int


class PickIn(BaseModel):
    sender_ids: list[UUID] = Field(max_length=sender_pool.MAX_ACCOUNTS)


def _shown(pool: list[PoolAccount]) -> dict:
    def one(a: PoolAccount) -> dict:
        full_cap = min(a.row['daily_limit'] or sending.DAILY_SEND_LIMIT, sending.DAILY_SEND_LIMIT)
        return {
            **{k: a.row[k] for k in ('id', 'provider', 'account_label', 'status')},
            'picked': a.picked,
            'state': a.state.state,
            'reason': a.state.reason,
            'blocker': a.blocker,
            'resumes_at': a.state.resumes_at,
            'sent_today': a.state.sent_today,
            'daily_cap': a.state.daily_cap,
            'warming_up': a.state.daily_cap < full_cap,
        }

    return {'senders': [one(a) for a in pool], 'max_senders': sender_pool.MAX_ACCOUNTS}


@router.get('/{campaign_id}/senders', response_model=CampaignSenders)
def senders(campaign_id: UUID, user: CurrentUser = Auth) -> dict:
    """The client's mailbox accounts, each picked or not, with its state, today's usage and cap, and its blocker."""
    switches = mailbox_kill_switches()
    with get_conn() as conn:
        return _shown(pool_state.accounts(conn, _own(conn, campaign_id, user.org_id), datetime.now(UTC), switches))


@router.put('/{campaign_id}/senders', response_model=CampaignSenders)
def pick_senders(campaign_id: UUID, body: PickIn, user: CurrentUser = Auth) -> dict:
    """The pool becomes exactly ``sender_ids``. 422 ``CAMPAIGN.SENDER_NOT_IN_PROJECT`` for an id that is not a mailbox
    account of the campaign's client; 422 ``CAMPAIGN.NO_SENDER`` when a live campaign with email steps would keep no
    account that can send."""
    switches = mailbox_kill_switches()
    picked = set(body.sender_ids)
    with get_conn() as conn:
        c = _own(conn, campaign_id, user.org_id, lock=True)
        if c['status'] in ('stopped', 'completed'):
            raise api_error(409, 'CAMPAIGN.INVALID_TRANSITION', f'a {c["status"]} campaign cannot be edited')
        pool = pool_state.accounts(conn, c, datetime.now(UTC), switches)
        if picked - {a.row['id'] for a in pool}:
            raise api_error(422, 'CAMPAIGN.SENDER_NOT_IN_PROJECT', "pick accounts of this campaign's client")
        pool = [replace(a, picked=a.row['id'] in picked) for a in pool]
        emails = any(s['channel'] == 'email' for s in c['steps'])
        if c['status'] != 'draft' and emails and not pool_state.can_send(pool):
            raise api_error(422, 'CAMPAIGN.NO_SENDER', 'pick a connected mailbox to send from')
        sender_pool.pick(conn, c['id'], sorted(picked, key=str))
        return _shown(pool)
