"""Settings from env. Only what the code reads; add fields when a caller does."""

from typing import Literal

from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CreditPack(BaseModel):  # m1-billing-admin
    id: str
    credits: int
    price_eur_cents: int
    stripe_price_id: str = ''  # empty = not purchasable (hidden)


class Plan(BaseModel):  # monthly subscription
    id: str
    credits: int  # granted on every paid invoice (signup + each renewal)
    price_eur_cents: int
    stripe_price_id: str = ''  # empty = not purchasable (hidden)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    # Infra
    database_url: str = 'postgresql://postgres:postgres@localhost:5432/zenbree'
    redis_url: str = 'redis://localhost:6379/0'
    web_origin: str = 'http://localhost:3000'
    betterstack_api_error_dsn: str = ''

    # Auth: Better Auth jwt plugin (issuer/audience default to the web base URL)
    auth_jwks_url: str = 'http://localhost:3000/api/auth/jwks'
    auth_issuer: str = 'http://localhost:3000'
    auth_audience: str = 'http://localhost:3000'

    # LLM
    llm_primary_provider: str = 'deepseek'
    chat_primary_provider: str = 'deepseek'
    llm_same_provider_retries: int = 0
    llm_same_provider_retry_backoff_seconds: float = 0.3
    deepseek_api_key: str = ''
    deepseek_base_url: str = 'https://api.deepseek.com/v1'
    deepseek_model: str = 'deepseek-v4-flash'
    deepseek_user_id_secret: str = ''
    anthropic_api_key: str = ''
    llm_model: str = 'claude-sonnet-4-6'  # Anthropic default model
    cf_ai_gateway_account_id: str = ''
    cf_ai_gateway_name: str = ''
    cf_ai_gateway_token: str = ''

    # Outreach send window (app.core.working_window)
    outreach_send_window_start_hour: int = 9
    outreach_send_window_end_hour: int = 18
    outreach_send_window_timezone: str = 'Europe/Rome'

    # m1-tools: vendors + LinkedIn (Unipile). Empty key = that vendor is inert.
    apollo_api_key: str = ''
    apollo_base_url: str = 'https://api.apollo.io/v1'
    aiark_api_key: str = ''
    aiark_base_url: str = 'https://api.ai-ark.com/api/developer-portal'
    treg_api_key: str = ''
    treg_base_url: str = ''  # the proxy's URL; empty = Treg inert
    serper_api_key: str = ''
    millionverifier_api_key: str = ''  # empty → MX-only email check
    unipile_dsn: str = ''  # e.g. https://api8.unipile.com:13851
    unipile_api_key: str = ''
    unipile_webhook_secret: str = ''  # static header Unipile sends (it does not sign)
    unipile_webhook_auth_header: str = 'Unipile-Auth'
    oauth_token_encryption_key: str = ''  # Fernet key: hosted-auth state + stored account id
    api_public_base_url: str = ''  # where Unipile calls back, e.g. https://api.zenbree.com

    # ── feature/m1-agent: web_search / fetch_page ──
    firecrawl_api_key: str = ''
    exa_enabled: bool = False  # Exa first, Firecrawl fallback (was a PostHog flag in the earlier implementation)
    exa_api_key: str = ''

    # m1-billing-admin
    stripe_secret_key: str = ''
    stripe_webhook_secret: str = ''
    # ponytail: placeholder packs; set BILLING_PACKS (JSON list) with real price ids.
    billing_packs: list[CreditPack] = [
        # ~€0.05 a credit, 1 credit = 1 email (2026-10-07); same amounts as the Stripe prices
        CreditPack(id='starter', credits=350, price_eur_cents=1900),
        CreditPack(id='growth', credits=1000, price_eur_cents=4900),
        CreditPack(id='scale', credits=3000, price_eur_cents=14900),
    ]
    # ponytail: placeholder plans; set BILLING_PLANS (JSON list) with real price ids.
    billing_plans: list[Plan] = [
        Plan(id='starter', credits=500, price_eur_cents=2900),
        Plan(id='pro', credits=1500, price_eur_cents=7900),
    ]
    # Costs are USD (llm_usage, vendor_calls), revenue EUR: margin uses this rate.
    eur_per_usd: float = 0.86

    # E7 sending mailboxes (#79): send-only OAuth apps. Empty = that provider is inert.
    # Redirect URI to register: <API_PUBLIC_BASE_URL>/v1/integrations/mailboxes/<provider>/callback
    google_oauth_client_id: str = ''
    google_oauth_client_secret: str = ''
    microsoft_oauth_client_id: str = ''
    microsoft_oauth_client_secret: str = ''

    # E7 email step (#81): signed Reply-To ``reply+c<campaign>.<hmac>@<domain>`` (app.core.reply_address),
    # also the List-Unsubscribe mailto. Leave EMPTY until the inbound receiver (#86) is live:
    # a Reply-To nobody receives would lose every reply. Empty = no Reply-To, replies go to the mailbox.
    outreach_reply_domain: str = ''
    outreach_reply_secret: str = ''  # also signs the unsubscribe link (#172, app.outreach.unsubscribe)

    # E8 inbound replies (#86): Resend inbound webhook signing secret (whsec_…) for POST /v1/inbound/email,
    # and Resend for reading received mail + the high-intent reply notice (same RESEND_API_KEY/EMAIL_FROM as web).
    outreach_inbound_webhook_secret: str = ''
    resend_api_key: str = ''
    email_from: str = ''

    # E13.1 HubSpot read gate (#120): one public HubSpot app, a per-user OAuth grant (never a global token).
    # The app (redirect URI, scopes, webhooks) is the HubSpot project in ops/hubspot. Empty = HubSpot is inert.
    hubspot_oauth_client_id: str = ''
    hubspot_oauth_client_secret: str = ''

    # Unsubscribe host (#220), e.g. https://u.zenbree.com: the footer link and the RFC 8058 one-click are
    # <origin>/x/<token>, served by the web. Empty: the page stays on WEB_ORIGIN/u/<token>, the one-click on
    # API_PUBLIC_BASE_URL. Same variable in the web (proxy.ts).
    unsubscribe_origin: str = ''

    # Web Push for notifications (#343): a browser alert with no Zenbree tab open. Empty keys = push is inert
    # and the web hides the enable button. Public key: URL-safe base64 of the uncompressed P-256 point.
    # Private key: the PEM or the raw URL-safe base64 key. Subject: a mailto: or https: contact for the push services.
    vapid_public_key: str = ''
    vapid_private_key: str = ''
    vapid_subject: str = ''

    # Profile photos (#503): resized WebP files in a Cloudflare R2 bucket, written through its S3 API and served by the
    # API. Any of the five empty = the feature is inert (upload answers 503 PHOTO.UNAVAILABLE).
    # Endpoint: https://<account id>.r2.cloudflarestorage.com. API_PUBLIC_BASE_URL (above) builds the photo's URL.
    r2_endpoint: str = ''
    r2_access_key_id: str = ''
    r2_secret_access_key: str = ''
    r2_photos_bucket: str = ''

    # Instantly (#929): each project connects its own workspace with its own API key, stored encrypted per project, so
    # there is no global key. The base URL is only for a test or a stand-in server (/verify); never a user input.
    instantly_api_base_url: str = 'https://api.instantly.ai'

    # Bounces (#747): Gmail pushes a mailbox's new mail to a Pub/Sub topic (projects/<project>/topics/<name>, in the
    # Google Cloud project of the OAuth client, with gmail-api-push@system.gserviceaccount.com as a publisher); its push
    # subscription calls API_PUBLIC_BASE_URL/v1/integrations/mailboxes/google/push?token=<GMAIL_PUSH_TOKEN>. Either
    # empty = Gmail bounces are not read. Microsoft needs no setting: API_PUBLIC_BASE_URL and OUTREACH_REPLY_SECRET.
    gmail_push_topic: str = ''
    gmail_push_token: str = ''

    # Log level (app.core.logging), switched by restarting the containers, not by a deploy. The same names and
    # variable as the web's lib/logger.ts, so one LOG_LEVEL in the root .env sets both.
    log_level: Literal['debug', 'info', 'warning', 'error'] = 'info'

    @field_validator('log_level', mode='before')
    @classmethod
    def _lowercase_log_level(cls, value: object) -> object:
        return value.lower() if isinstance(value, str) else value


settings = Settings()
