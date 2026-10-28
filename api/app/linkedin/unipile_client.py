"""Unipile V1 adapter. All transport failures become typed Result errors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx
import structlog

from app.core.config import settings
from app.core.errors import Result, err, is_err, ok

logger = structlog.get_logger()

# From the earlier implementation app/outreach/domain/{linkedin,sender_intro}.py — only what this client reads.
PROVIDER_LIMIT = 'OUTREACH.LINKEDIN_PROVIDER_LIMIT'
#: The error-context key carrying Unipile's ``type`` enum. Never the body.
PROVIDER_TYPE_KEY = 'provider_type'
#: 4xx ``type`` values that mean a LinkedIn limit (→ :data:`PROVIDER_LIMIT`).
PROVIDER_LIMIT_TYPES: frozenset[str] = frozenset(
    {
        'errors/cannot_resend_yet',
        'errors/cannot_resend_within_24hrs',
        'errors/limit_exceeded',
        'errors/connection_limit_reached',
        'errors/too_many_requests',
    }
)


@dataclass(frozen=True, slots=True)
class LinkedInAccountRead:
    """One read of the connected account: its plan features and its own name."""

    premium_features: list[str] | None
    name: str | None


#: Enough for the ordinary REST calls — resolve a profile, send an invite,
#: read an account.
_DEFAULT_TIMEOUT_S = 30.0

#: And what a LOGIN needs.
#:
#: Attaching an account is not a REST call: Unipile drives a real browser
#: session against LinkedIn, waits for the page, and only then answers with an
#: account or a challenge. Measured against the live API, that ran past 30s
#: and came back `ReadTimeout` every time — which the UI then reported as the
#: channel not being configured, on an account where it was.
_LOGIN_TIMEOUT_S = 180.0

#: And what a DM CARRYING FILES gets: up to 2.5 MB goes up to Unipile, and
#: Unipile hands it on to LinkedIn before it answers. A ReadTimeout on that
#: call is read as "may have been delivered" (``delivery_ambiguous``) and the
#: row is kept ``sent`` — the safe direction, but a false one when the upload
#: was merely slow, so the answer is given longer to come back. A DM with no
#: files keeps the default.
_UPLOAD_TIMEOUT_S = 90.0


def _iso_z(moment: datetime) -> str:
    """Format for Unipile's `YYYY-MM-DDTHH:MM:SS.sssZ` schema.

    Milliseconds and a literal `Z`. Python's `.isoformat()` gives microseconds
    and `+00:00`, which the API rejects as an invalid parameter rather than
    coercing.
    """
    return moment.astimezone(UTC).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


#: The paths that LOG IN. There, a 401 with ``errors/invalid_credentials`` is the
#: password the user just typed; anywhere else it is a stored session that
#: stopped working.
_LOGIN_PATH_PREFIX = '/api/v1/accounts'


def _provider_error_type(response: httpx.Response) -> str:
    """Unipile's ``type`` for a failure, or ``''`` — the ONE extractor .

    An enum value such as ``errors/limit_exceeded``: safe to log and to carry on
    an error's context under ``PROVIDER_TYPE_KEY``, unlike the rest of the body,
    which can carry the request back. Classified against the sets in
    ``app.outreach.domain.linkedin``, never here.
    """
    try:
        body = response.json()
    except ValueError:
        return ''
    return str(body.get('type') or '')[:100] if isinstance(body, dict) else ''


def _rejection(response: httpx.Response) -> Result[dict]:
    """A 4xx that is neither auth nor 429: a limit on the account, or a refusal of this operation."""
    provider_type = _provider_error_type(response)
    context = {'provider_status': response.status_code, PROVIDER_TYPE_KEY: provider_type}
    if provider_type in PROVIDER_LIMIT_TYPES:
        return err(
            PROVIDER_LIMIT,
            detail='LinkedIn refused: this account reached one of its limits',
            **context,
        )
    return err(
        'OUTREACH.PROVIDER_REJECTED',
        detail='LinkedIn rejected the requested operation',
        **context,
    )


def _is_provider_credential_error(response: httpx.Response) -> bool:
    """Whether a 401 names the user's LinkedIn credentials, not our API key.

    Read from the body rather than assumed from the path: an api-key failure on
    the same endpoint has to keep saying so, or a misconfigured deployment looks
    to every user like their own password being refused.
    """
    return _provider_error_type(response) == 'errors/invalid_credentials'


def _auth_failure(response: httpx.Response, path: str) -> Result[dict]:
    """A 401/403, told apart by Unipile's ``type``.

    401 means different things on this API, and conflating them tells the user
    the wrong one every time. On the login endpoints, ``invalid_credentials``
    is THEIR password: reported as a channel-auth failure it surfaced as
    "LinkedIn isn't wired up on this account", sending someone to check a
    configuration they cannot see. Everywhere else every 401/403 is
    ``INTEGRATIONS.AUTH`` WITH its ``type``, and ``domain.linkedin.session_lost``
    decides whether it is the user's session (pause, ask to reconnect) or ours —
    a rotated API key answers 401 for every account at once .
    """
    provider_type = _provider_error_type(response)
    if _is_provider_credential_error(response) and path.startswith(_LOGIN_PATH_PREFIX):
        return err(
            'OUTREACH.PROVIDER_REJECTED',
            detail='LinkedIn did not accept those credentials',
            **{PROVIDER_TYPE_KEY: provider_type},
        )
    return err(
        'INTEGRATIONS.AUTH',
        detail='LinkedIn channel authentication failed',
        provider_status=response.status_code,
        **{PROVIDER_TYPE_KEY: provider_type},
    )


class UnipileClient:
    def __init__(
        self,
        *,
        dsn: str | None = None,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        configured_dsn = dsn if dsn is not None else settings.unipile_dsn
        self._base_url = configured_dsn.rstrip('/')
        self._api_key = api_key if api_key is not None else settings.unipile_api_key
        self._client = client

    def is_configured(self) -> bool:
        return bool(
            self._base_url
            and self._base_url != 'not-configured'
            and self._api_key
            and self._api_key != 'not-configured'
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> Result[dict]:
        if not self.is_configured():
            return err('INTEGRATIONS.NOT_CONFIGURED', detail='LinkedIn channel is not configured')
        headers = dict(kwargs.pop('headers', {}))
        headers['X-API-KEY'] = self._api_key
        owned = self._client is None
        timeout = kwargs.pop('timeout', None) or _DEFAULT_TIMEOUT_S
        client = self._client or httpx.AsyncClient(timeout=timeout)
        try:
            response = await client.request(method, f'{self._base_url}{path}', headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            logger.warning('unipile.transport_failed', error_type=type(exc).__name__)
            return err('INTEGRATIONS.TRANSPORT', detail='LinkedIn is temporarily unreachable')
        finally:
            if owned:
                await client.aclose()
        if response.status_code >= 400:
            # WHICH failure, in the log, before it is turned into a Result.
            #
            # Every non-transport failure here becomes one of five codes, and
            # three of them leave as HTTP 503. So an operator reading the log
            # saw "503" and could not tell a rate limit from an unconfigured
            # channel from a rejected login — the only way to find out was to
            # make another call, which on a rate limit is the one thing that
            # makes it worse. Never the body: it carries the credentials.
            logger.warning(
                'unipile.request_failed',
                path=path,
                provider_status=response.status_code,
                # The enum, never the body: it is what the first live account
                # has to be read by to learn which refusals are limits.
                provider_type=_provider_error_type(response),
            )
        if response.status_code in (401, 403):
            return _auth_failure(response, path)
        if response.status_code == 429:
            return err('INTEGRATIONS.RATE_LIMITED', detail='LinkedIn rate limit reached')
        if response.status_code >= 500:
            return err('INTEGRATIONS.TRANSPORT', detail='LinkedIn is temporarily unavailable')
        if response.status_code >= 400:
            return _rejection(response)
        if response.status_code == 204 or not response.content:
            return ok({})
        try:
            payload = response.json()
        except ValueError:
            return err('INTEGRATIONS.INVALID_JSON', detail='LinkedIn channel returned invalid JSON')
        return ok(payload if isinstance(payload, dict) else {'items': payload})

    async def connect_linkedin(self, *, username: str, password: str) -> Result[dict]:
        """Attach a LinkedIn account from credentials the user typed into OUR form.

        The alternative — and what this codebase did first — is
        :meth:`create_hosted_link`, where Unipile collects the credentials on
        its own page and we never see them. This exists because a full-page
        redirect mid-onboarding loses people, and it is a deliberate trade
        made with the cost stated: the password reaches our process. It is
        forwarded and dropped — never stored, never logged, never placed in an
        error detail.

        NEITHER route is compliant with LinkedIn's User Agreement, which
        forbids automated access and credential sharing, and no route is:
        LinkedIn's public APIs do not offer invites or DMs to anyone outside
        a narrow partner programme. What differs between the two is who HOLDS
        the secret, and on this one it is us.

        ``Ok`` carries either a connected account or a CHECKPOINT — Unipile
        answers 2FA, an in-app approval or a captcha as an ordinary body with
        ``object: "Checkpoint"``, not as an error, and the caller has to
        finish it with :meth:`solve_checkpoint`.
        """
        return await self._request(
            'POST',
            '/api/v1/accounts',
            # `_request` logs only the exception type and never the body,
            # which is what keeps this call out of the logs. Anything added
            # there that echoes kwargs would leak a password.
            json={'provider': 'LINKEDIN', 'username': username, 'password': password},
            timeout=_LOGIN_TIMEOUT_S,
        )

    async def solve_checkpoint(
        self,
        *,
        account_id: str,
        code: str,
        provider: str = 'LINKEDIN',
    ) -> Result[dict]:
        """Finish a connection LinkedIn interrupted with a challenge.

        A second checkpoint can follow the first — an OTP after an in-app
        approval is ordinary — so the caller loops on whatever comes back
        rather than assuming one round settles it.
        """
        return await self._request(
            'POST',
            '/api/v1/accounts/checkpoint',
            json={'provider': provider, 'account_id': account_id, 'code': code},
            # A code is checked by the same browser session that is holding
            # the half-finished login open.
            timeout=_LOGIN_TIMEOUT_S,
        )

    async def create_hosted_link(
        self,
        *,
        state: str,
        notification_name: str,
        notify_url: str,
        success_redirect_url: str,
        failure_redirect_url: str,
        reconnect_account: str = '',
    ) -> Result[str]:
        """A hosted-auth link: a new account, or ``reconnect_account`` re-authorised.

        Reconnecting keeps the account id . A ``create`` for an
        owner who already had one mints a NEW Unipile account, and the quota
        ledger is keyed by account id — so the rolling 24h/7d counts started
        from zero and the week's invitations could be sent a second time from
        the same LinkedIn profile. Unipile's reconnect schema takes
        ``reconnect_account`` and does not allow ``providers``.
        """
        kind: dict[str, Any] = (
            {'type': 'reconnect', 'reconnect_account': reconnect_account}
            if reconnect_account
            else {'type': 'create', 'providers': ['LINKEDIN']}
        )
        result = await self._request(
            'POST',
            '/api/v1/hosted/accounts/link',
            json={
                **kind,
                'api_url': self._base_url,
                # Unipile validates this against "YYYY-MM-DDTHH:MM:SS.sssZ".
                # `.isoformat()` gives neither half of that: microseconds
                # instead of milliseconds, and a "+00:00" offset instead of
                # "Z". The link request came back 400 invalid_parameters,
                # which this adapter reported as PROVIDER_REJECTED — a
                # message that sends someone to check their LinkedIn
                # password over a date we formatted wrong.
                'expiresOn': _iso_z(datetime.now(UTC) + timedelta(minutes=15)),
                'notify_url': f'{notify_url}?state={state}',
                # Unipile echoes this in the hosted-auth callback. It is an
                # owned chat-session id when connect started from chat, or the
                # user id for the REST fallback; the signed state remains the
                # callback authentication primitive.
                'name': notification_name,
                'success_redirect_url': success_redirect_url,
                'failure_redirect_url': failure_redirect_url,
            },
        )
        if is_err(result):
            return result
        url = str(result.value.get('url') or '')
        if not url:
            return err('INTEGRATIONS.INVALID_JSON', detail='LinkedIn connection URL is unavailable')
        return ok(url)

    async def delete_account(self, *, account_id: str) -> Result[None]:
        """Revoke our LinkedIn account at Unipile; an already absent id is done."""
        removed = await self._request(
            'DELETE',
            f'/api/v1/accounts/{quote(account_id, safe="")}',
        )
        if is_err(removed):
            if removed.error.context.get('provider_status') == 404:
                return ok(None)
            return removed
        return ok(None)

    async def health_check(self) -> Result[dict]:
        return await self._request('GET', '/api/v1/accounts', params={'limit': 1})

    async def read_account(
        self,
        *,
        account_id: str,
        timeout: float = 4.0,  # noqa: ASYNC109 — per-request httpx timeout, not a cancellation scope
    ) -> Result[LinkedInAccountRead]:
        """The account's plan features and its own name, from ONE read.

        The short timeout keeps an account page from waiting on Unipile.
        ``name`` is the Account's
        top-level ``name``, as Unipile reports it; whether it can be compared
        against a self-introduction is ``sender_intro.account_name``'s call.
        """
        fetched = await self._request(
            'GET',
            f'/api/v1/accounts/{quote(account_id, safe="")}',
            timeout=timeout,
        )
        if is_err(fetched):
            return fetched
        params = fetched.value.get('connection_params')
        im = params.get('im') if isinstance(params, dict) else None
        features = im.get('premiumFeatures') if isinstance(im, dict) else None
        name = fetched.value.get('name')
        return ok(
            LinkedInAccountRead(
                premium_features=(
                    sorted({str(item).strip().lower()[:40] for item in features})
                    if isinstance(features, list)
                    else None
                ),
                name=(name.strip()[:200] or None) if isinstance(name, str) else None,
            )
        )

    async def resolve_profile(self, *, account_id: str, identifier: str) -> Result[dict]:
        return await self._request(
            'GET',
            f'/api/v1/users/{identifier}',
            params={'account_id': account_id, 'notify': 'false'},
        )

    async def send_invitation(
        self,
        *,
        account_id: str,
        provider_id: str,
        message: str,
    ) -> Result[dict]:
        payload: dict[str, str] = {'account_id': account_id, 'provider_id': provider_id}
        if message:
            payload['message'] = message[:300]
        return await self._request('POST', '/api/v1/users/invite', json=payload)

    async def cancel_invitation(self, *, account_id: str, invitation_id: str) -> Result[dict]:
        return await self._request(
            'DELETE',
            f'/api/v1/users/invite/sent/{invitation_id}',
            params={'account_id': account_id},
        )

    async def send_dm(
        self,
        *,
        account_id: str,
        provider_id: str,
        text: str,
        attachments: tuple[tuple[str, str, bytes], ...] = (),
    ) -> Result[dict]:
        # One `attachments` part per file, after the attendee: the multipart
        # field `POST /api/v1/chats` documents (an array of binaries). With no
        # files the list is the one-part request this method always sent.
        files: list[tuple[str, tuple[Any, ...]]] = [
            ('attendees_ids', (None, provider_id)),
        ]
        files.extend(
            ('attachments', (filename, content, content_type)) for filename, content_type, content in attachments
        )
        return await self._request(
            'POST',
            '/api/v1/chats',
            data={'account_id': account_id, 'text': text},
            files=files,
            timeout=_UPLOAD_TIMEOUT_S if attachments else None,
        )

    async def list_posts(self, *, account_id: str, provider_id: str) -> Result[dict]:
        return await self._request(
            'GET',
            f'/api/v1/users/{provider_id}/posts',
            params={'account_id': account_id, 'limit': 10},
        )

    async def comment_post(self, *, account_id: str, post_id: str, text: str) -> Result[dict]:
        return await self._request(
            'POST',
            f'/api/v1/posts/{quote(post_id, safe="")}/comments',
            # Documented as multipart/form-data, like the chats endpoint.
            files=[('account_id', (None, account_id)), ('text', (None, text))],
        )

    async def react_post(self, *, account_id: str, post_id: str, reaction: str) -> Result[dict]:
        return await self._request(
            'POST',
            '/api/v1/posts/reaction',
            json={'account_id': account_id, 'post_id': post_id, 'reaction_type': reaction},
        )

    async def search(
        self,
        *,
        account_id: str,
        body: dict[str, Any],
        limit: int = 25,
        cursor: str | None = None,
    ) -> Result[dict]:
        """``POST /api/v1/linkedin/search``: classic or sales_navigator, people or companies.

        ``body`` is Unipile's own shape (``api``, ``category``, ``keywords``,
        numeric-id filters, or a LinkedIn search ``url``). Answers
        ``{items, paging, cursor}``.
        """
        params: dict[str, Any] = {'account_id': account_id, 'limit': max(1, min(limit, 100))}
        if cursor:
            params['cursor'] = cursor
        return await self._request('POST', '/api/v1/linkedin/search', params=params, json=body)

    async def search_parameters(
        self,
        *,
        account_id: str,
        type: str,
        keywords: str,
        limit: int = 3,
    ) -> Result[dict]:
        """Numeric ids for a search filter (``type`` = LOCATION, INDUSTRY, COMPANY…)."""
        return await self._request(
            'GET',
            '/api/v1/linkedin/search/parameters',
            params={'account_id': account_id, 'type': type, 'keywords': keywords, 'limit': limit},
        )
