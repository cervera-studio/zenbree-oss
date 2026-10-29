"""OAuth providers (mailboxes, HubSpot): endpoints and scopes. Port of the earlier implementation ``integrations/domain/providers.py``.

Replies come back through the signed ``Reply-To`` (``app.core.reply_address``), never by reading the mailbox
(#79). The read scopes (owner decision on #747, 2026-10-16) are only for the delivery-status notifications the
mailbox receives for what it sent (``app.integrations.mailbox_push``). ``gmail.send`` is a *sensitive* Google
scope; ``gmail.readonly`` is *restricted* (verification plus an annual security assessment).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True, kw_only=True)
class OAuthProvider:
    """What :class:`app.integrations.oauth_client.OAuthClient` needs to run the authorization-code flow."""

    name: str
    authorize_url: str
    token_url: str
    scopes: tuple[str, ...]
    extra_authorize_params: tuple[tuple[str, str], ...] = ()
    # Revokes the grant on disconnect. Microsoft has no per-app refresh-token revoke.
    revoke_url: str = ''
    # HubSpot's revoke also takes the app's client id/secret in the form.
    revoke_with_client_auth: bool = False
    # HubSpot documents no PKCE: its grant is bound to the user by the signed state alone.
    pkce: bool = True
    profile_url: str = ''
    # Keys tried in order: Microsoft leaves ``mail`` null on personal accounts.
    profile_email_keys: tuple[str, ...] = ()
    # The provider's stable id for the account in the profile (#307): what tells one mailbox from another. The
    # address is not: Microsoft's ``mail`` is editable by a tenant admin and absent on personal accounts.
    profile_subject_key: str = ''


@dataclass(frozen=True, slots=True, kw_only=True)
class MailboxProvider(OAuthProvider):
    # The scope that authorises a send; a consent screen lets the user untick it.
    send_scope: str
    # The scope that reads the bounces; a mailbox connected before #747, or with it unticked, sends without it.
    read_scope: str
    # Both take the raw RFC 822 message: Gmail as base64url inside JSON, Graph as base64 text/plain.
    send_url: str
    # Key of the provider's thread handle in the send response; Graph answers 202 with no body.
    thread_id_key: str = ''

    def grants_send(self, granted: list[str] | tuple[str, ...]) -> bool:
        return _grants(self.send_scope, granted)

    def grants_read(self, granted: list[str] | tuple[str, ...]) -> bool:
        return _grants(self.read_scope, granted)


def _grants(scope: str, granted: list[str] | tuple[str, ...]) -> bool:
    """Microsoft may echo ``Mail.Send`` without the Graph resource prefix."""
    want = scope.rsplit('/', 1)[-1].lower()
    return any(g.rsplit('/', 1)[-1].lower() == want for g in granted)


GOOGLE: Final = MailboxProvider(
    name='google',
    authorize_url='https://accounts.google.com/o/oauth2/v2/auth',
    token_url='https://oauth2.googleapis.com/token',  # noqa: S106 — public endpoint URL, not a secret
    scopes=(
        'openid',
        'email',
        'https://www.googleapis.com/auth/gmail.send',
        'https://www.googleapis.com/auth/gmail.readonly',
    ),
    send_scope='https://www.googleapis.com/auth/gmail.send',
    read_scope='https://www.googleapis.com/auth/gmail.readonly',
    profile_url='https://openidconnect.googleapis.com/v1/userinfo',
    profile_email_keys=('email',),
    profile_subject_key='sub',  # OpenID subject
    send_url='https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
    thread_id_key='threadId',
    # Google returns a refresh token only with both, and re-issues one only on forced consent.
    extra_authorize_params=(('access_type', 'offline'), ('prompt', 'consent')),
    revoke_url='https://oauth2.googleapis.com/revoke',
)

MICROSOFT: Final = MailboxProvider(
    name='microsoft',
    authorize_url='https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
    token_url='https://login.microsoftonline.com/common/oauth2/v2.0/token',  # noqa: S106 — public endpoint URL
    scopes=(
        'offline_access',
        'User.Read',
        'https://graph.microsoft.com/Mail.Send',
        'https://graph.microsoft.com/Mail.Read',
    ),
    send_scope='https://graph.microsoft.com/Mail.Send',
    read_scope='https://graph.microsoft.com/Mail.Read',
    profile_url='https://graph.microsoft.com/v1.0/me',
    profile_email_keys=('mail', 'userPrincipalName'),
    profile_subject_key='id',  # the user's directory object id (oid)
    send_url='https://graph.microsoft.com/v1.0/me/sendMail',
)

MAILBOX_PROVIDERS: Final[dict[str, MailboxProvider]] = {GOOGLE.name: GOOGLE, MICROSOFT.name: MICROSOFT}

# E13.1 read gate (#120): read-only CRM scopes, all required (HubSpot shows no per-scope untick).
# Writing back (#121) widens this tuple; existing connections then need a reconnect.
HUBSPOT_READ_SCOPES: Final = (
    'crm.objects.contacts.read',
    'crm.objects.companies.read',
    'crm.objects.deals.read',
    'crm.objects.owners.read',
    'communication_preferences.read',
)

# E13.2 (#121): asked as optional scopes, so a portal that cannot give them still connects for the read gate
# and a connection made before them keeps working. HubSpot's consent screen lists optional scopes as checkboxes,
# one by one, so a grant may carry one and not the other: contacts is the one write-back needs, companies only
# adds the company of a contact (app.crm.write_back).
HUBSPOT_CONTACTS_WRITE: Final = 'crm.objects.contacts.write'
HUBSPOT_COMPANIES_WRITE: Final = 'crm.objects.companies.write'
HUBSPOT_WRITE_SCOPES: Final = (HUBSPOT_CONTACTS_WRITE, HUBSPOT_COMPANIES_WRITE)

HUBSPOT: Final = OAuthProvider(
    name='hubspot',
    authorize_url='https://app.hubspot.com/oauth/authorize',
    token_url='https://api.hubspot.com/oauth/v3/token',  # noqa: S106 — public endpoint URL, not a secret
    scopes=('oauth', *HUBSPOT_READ_SCOPES),
    extra_authorize_params=(('optional_scope', ' '.join(HUBSPOT_WRITE_SCOPES)),),
    revoke_url='https://api.hubapi.com/oauth/2026-03/token/revoke',
    revoke_with_client_auth=True,
    pkce=False,
)
