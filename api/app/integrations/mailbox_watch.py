"""Starting, renewing and stopping a sending mailbox's bounce watch, and what a send leaves for its bounces (#747).

The dispatcher calls ``keep_watching`` before a send and ``delivered`` after it; a disconnect calls ``unwatch``.
``app.integrations.mailbox_push`` receives the pushes and reads the bounces.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
import structlog

from app.core.config import settings
from app.core.db import get_conn
from app.core.errors import is_err
from app.core.mail_threading import normalise_message_id
from app.integrations import token_crypto
from app.integrations.mailbox_fetch import GMAIL_API, GRAPH_API
from app.integrations.mailbox_push import client_state, gmail_on, graph_on, provider_http
from app.integrations.providers import MAILBOX_PROVIDERS
from app.outreach import bounces

logger = structlog.get_logger()

# Graph's longest mail subscription is 10,080 minutes; an hour short of it so a slow clock is never refused.
GRAPH_WATCH = timedelta(days=7) - timedelta(hours=1)
RENEW_WHEN_LEFT = timedelta(days=6)
# How far back a Graph sent copy of the send just made can be.
SENT_WINDOW = timedelta(minutes=2)


async def _watch_gmail(http: httpx.AsyncClient, mb: dict, now: datetime) -> dict | None:
    if not gmail_on():
        return None
    resp = await http.post(f'{GMAIL_API}/watch', json={'topicName': settings.gmail_push_topic, 'labelIds': ['INBOX']})
    if resp.status_code != 200:
        logger.warning('mailbox_push.watch_refused', provider='google', status=resp.status_code)
        return None
    body = resp.json()
    return {
        'id': None,
        'expires_at': datetime.fromtimestamp(int(body['expiration']) / 1000, UTC),
        'history_id': str(body['historyId']),
    }


async def _watch_graph(http: httpx.AsyncClient, mb: dict, now: datetime) -> dict | None:
    if not graph_on():
        return None
    expires = now + GRAPH_WATCH
    when = {'expirationDateTime': expires.isoformat().replace('+00:00', 'Z')}
    if mb['inbound_subscription_id']:
        resp = await http.patch(f'{GRAPH_API}/subscriptions/{mb["inbound_subscription_id"]}', json=when)
        if resp.status_code == 200:
            return {'id': mb['inbound_subscription_id'], 'expires_at': expires, 'history_id': None}
    connection, org = str(mb['id']), str(mb['organization_id'])
    url = f'{settings.api_public_base_url.rstrip("/")}/v1/integrations/mailboxes/microsoft/push?'
    resp = await http.post(
        f'{GRAPH_API}/subscriptions',
        json={
            'changeType': 'created',
            'notificationUrl': url + urlencode({'c': connection, 'o': org}),
            'resource': "me/mailFolders('inbox')/messages",
            'clientState': client_state(connection, org),
            **when,
        },
    )
    if resp.status_code != 201:
        logger.warning('mailbox_push.watch_refused', provider='microsoft', status=resp.status_code)
        return None
    return {'id': str(resp.json()['id']), 'expires_at': expires, 'history_id': None}


async def keep_watching(mb: dict, access_token: str, now: datetime) -> None:
    """Before a send from ``mb`` (a ``user_oauth_connections`` row): start or renew its watch. Never raises: a
    mailbox that cannot be watched still sends."""
    expires = mb['inbound_subscription_expires_at']
    if not MAILBOX_PROVIDERS[mb['provider']].grants_read(mb['granted_scopes'] or []) or (
        expires and expires - now > RENEW_WHEN_LEFT
    ):
        return
    watch = _watch_gmail if mb['provider'] == 'google' else _watch_graph
    try:
        async with provider_http(access_token) as http:
            watched = await watch(http, mb, now)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.warning('mailbox_push.watch_failed', provider=mb['provider'], error_type=type(exc).__name__)
        return
    if watched is None:
        return
    with get_conn() as conn:
        # The cursor stays where it was: a renewal must not skip what arrived since the last read.
        conn.execute(
            'UPDATE user_oauth_connections SET inbound_subscription_id = %s, inbound_subscription_expires_at = %s,'
            ' inbound_history_id = coalesce(inbound_history_id, %s) WHERE id = %s',
            (watched['id'], watched['expires_at'], watched['history_id'], mb['id']),
        )
    mb['inbound_subscription_expires_at'] = watched['expires_at']


async def _provider_message_id(http: httpx.AsyncClient, mb: dict, send: dict, receipt_id: str | None) -> str:
    """The Message-ID the provider gave a send it just accepted: Gmail's sent message by its id; Graph's sent copy
    (``_graph_sent_id``). ``''`` when it cannot tell."""
    if mb['provider'] == 'google':
        if not receipt_id:
            return ''
        resp = await http.get(
            f'{GMAIL_API}/messages/{receipt_id}', params={'format': 'metadata', 'metadataHeaders': ['Message-ID']}
        )
        headers = resp.json().get('payload', {}).get('headers', []) if resp.status_code == 200 else []
        return next((h['value'] for h in headers if h['name'].lower() == 'message-id'), '')
    return await _graph_sent_id(http, send)


async def _graph_sent_id(http: httpx.AsyncClient, send: dict) -> str:
    """Graph answers a send with no id. The sent copy with the Message-ID we wrote, when Exchange kept it; else the
    one sent copy of this minute to this one recipient under this subject: an earlier step or an email written by hand
    is older or names another subject."""
    sent_items = f"{GRAPH_API}/me/mailFolders('sentitems')/messages"
    ours = send['message_id'] or ''
    if ours:
        quoted = ours.replace("'", "''")  # OData string literal
        resp = await http.get(
            sent_items,
            params={'$filter': f"internetMessageId eq '{quoted}'", '$select': 'internetMessageId', '$top': '1'},
        )
        if resp.status_code == 200 and resp.json().get('value'):
            return ours
    since = (datetime.now(UTC) - SENT_WINDOW).isoformat().replace('+00:00', 'Z')
    resp = await http.get(
        sent_items,
        params={
            '$filter': f'sentDateTime ge {since}',
            '$orderby': 'sentDateTime desc',
            '$select': 'internetMessageId,toRecipients,subject',
            '$top': '5',
        },
    )
    to, subject = send['recipient'].strip().lower(), (send['subject'] or '').strip().lower()
    matches = [
        str(item.get('internetMessageId') or '')
        for item in (resp.json().get('value', []) if resp.status_code == 200 else [])
        if [((r.get('emailAddress') or {}).get('address') or '').lower() for r in item.get('toRecipients') or []]
        == [to]
        and (item.get('subject') or '').strip().lower() == subject
    ]
    # ponytail: Exchange saves the sent copy a moment later; then the Message-ID we wrote is the match. Two copies
    # alike within the window are ambiguous: none is taken.
    return matches[0] if len(matches) == 1 else ''


async def delivered(mb: dict, access_token: str, send: dict, receipt_id: str | None) -> None:
    """After the provider accepted ``send`` from ``mb``: store the Message-ID it gave the email, which its bounces
    quote (Gmail and Exchange may replace ours), and recheck the campaign's bounce rate. Never raises."""
    logger.info('campaign.send.delivered', send_id=str(send['id']), provider=mb['provider'])
    if MAILBOX_PROVIDERS[mb['provider']].grants_read(mb['granted_scopes'] or []):
        try:
            async with provider_http(access_token) as http:
                message_id = normalise_message_id(await _provider_message_id(http, mb, send, receipt_id))
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning('mailbox_push.message_id_unread', provider=mb['provider'], error_type=type(exc).__name__)
            message_id = ''
        if message_id:
            with get_conn() as conn:
                conn.execute(
                    'UPDATE outreach_campaign_sends SET provider_message_id = %s WHERE id = %s',
                    (message_id, send['id']),
                )
    await asyncio.to_thread(bounces.recheck, str(send['campaign_id']), str(send['organization_id']))


async def stop_watching(mb: dict, access_token: str) -> None:
    """On disconnect: Gmail stops pushing the account, Graph deletes the subscription. Best-effort: a watch left
    behind expires within 7 days, and a push for a disconnected mailbox reads nothing."""
    try:
        async with provider_http(access_token) as http:
            if mb['provider'] == 'google':
                await http.post(f'{GMAIL_API}/stop')
            elif mb['inbound_subscription_id']:
                await http.delete(f'{GRAPH_API}/subscriptions/{mb["inbound_subscription_id"]}')
    except httpx.HTTPError as exc:
        logger.warning('mailbox_push.stop_failed', provider=mb['provider'], error_type=type(exc).__name__)


async def unwatch(row: dict) -> None:
    """Before a disconnect revokes ``row``'s grant: stop its watch, unless another live row of the same Google
    account (one watch per account) still needs it. Never raises."""
    from app.integrations.member_release import has_live_sibling  # member_release imports the senders' modules
    from app.integrations.oauth_client import OAuthClient

    provider = MAILBOX_PROVIDERS.get(row['provider'])
    if not provider or not row['inbound_subscription_expires_at'] or not row['refresh_token_encrypted']:
        return
    if provider.name == 'google' and row['provider_account_id'] and await asyncio.to_thread(has_live_sibling, row):
        return
    token = token_crypto.decrypt(row['refresh_token_encrypted'])
    grant = None if is_err(token) else await OAuthClient(provider).refresh(token.value)
    if grant is not None and not is_err(grant):
        await stop_watching(row, grant.value.access_token)
