from datetime import datetime
from functools import cache
from typing import Literal

import redis
import sentry_sdk as betterstack_sdk
import structlog
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sentry_sdk.transport import Transport
from sentry_sdk.types import Breadcrumb, BreadcrumbHint, Event, Hint

from app.admin.ban import router as admin_ban_router
from app.admin.chats import router as admin_chats_router
from app.admin.jobs import router as admin_jobs_router
from app.admin.routes import router as admin_router
from app.agent.routes import router as agent_router
from app.agent.session_delete import router as session_delete_router
from app.audience.routes import router as audience_router
from app.auth.account_delete import router as account_delete_router
from app.auth.api_key_routes import router as api_keys_router
from app.auth.middleware import CurrentUser, SystemReason, current_user_or_api_key, public, require_admin
from app.auth.photo import MAX_BYTES as PHOTO_MAX_BYTES
from app.auth.photo import router as photo_router
from app.automations.inbound import router as automation_hooks_router
from app.automations.routes import router as automations_router
from app.billing.credit_split import credit_split
from app.billing.invoices import router as billing_invoices_router
from app.billing.ledger import router as billing_ledger_router
from app.billing.plan_change import router as plan_change_router
from app.billing.profile import router as billing_profile_router
from app.billing.routes import router as billing_router
from app.billing.subscription import _live_subscription
from app.core.body_limit import BodyLimit
from app.core.config import settings
from app.core.db import get_conn
from app.core.error_handlers import install as install_error_handlers
from app.core.logging import configure_logging
from app.crm.webhooks import router as hubspot_webhooks_router
from app.exclusions.routes import router as exclusions_router
from app.home.routes import router as home_router
from app.integrations.hubspot import router as hubspot_router
from app.integrations.instantly import router as instantly_router
from app.integrations.instantly_accounts import router as instantly_accounts_router
from app.integrations.instantly_sync.webhook import router as instantly_webhook_router
from app.integrations.mailbox_push import router as mailbox_push_router
from app.integrations.mailboxes import router as mailboxes_router
from app.integrations.member_release import router as member_release_router
from app.integrations.senders import router as senders_router
from app.integrations.webhook_deliveries import router as webhook_deliveries_router
from app.integrations.webhooks import router as webhooks_router
from app.linkedin.api import router as linkedin_router
from app.lists.bulk_routes import router as list_bulk_router
from app.lists.csv_import import MAX_BYTES as CSV_MAX_BYTES
from app.lists.csv_routes import router as csv_import_router
from app.lists.export_routes import router as list_export_router
from app.lists.routes import router as lists_router
from app.lists.row_phone import router as row_phone_router
from app.lists.snippet_routes import router as snippet_router
from app.memory.routes import router as memory_router
from app.notifications.routes import router as notifications_router
from app.onboarding.routes import router as onboarding_router
from app.outreach.activity import router as activity_router
from app.outreach.field_routes import router as campaign_fields_router
from app.outreach.inbox_routes import router as inbox_router
from app.outreach.pool_routes import router as campaign_senders_router
from app.outreach.routes import router as campaigns_router
from app.outreach.snippet_rows import router as campaign_snippet_rows_router
from app.outreach.stop_routes import router as instantly_stops_router
from app.outreach.test_email_routes import router as campaign_test_email_router
from app.outreach.unsubscribe_routes import router as unsubscribe_router
from app.outreach.variant_routes import router as campaign_variants_router
from app.projects.routes import router as projects_router
from app.reports.routes import router as reports_router
from app.scheduling.routes import router as scheduling_router
from app.segments.routes import router as segments_router
from app.views.routes import router as views_router
from app.watchlists.routes import router as watchlists_router


def _drop_query_string(event: Event, hint: Hint) -> Event:
    # The SDK sends the raw query string even without PII: OAuth callbacks carry `code`/`state` in it (#452).
    (event.get('request') or {}).pop('query_string', None)
    return event


def _strip_breadcrumb_query(crumb: Breadcrumb, hint: BreadcrumbHint) -> Breadcrumb:
    # Outgoing http breadcrumbs keep the vendor URL's query and fragment, where some vendors take the API key (#452).
    data = crumb.get('data')
    if crumb.get('type') == 'http' and isinstance(data, dict):
        data.pop('http.query', None)
        data.pop('http.fragment', None)
        if isinstance(data.get('url'), str):
            data['url'] = data['url'].split('?', 1)[0].split('#', 1)[0]
    return crumb


def init_error_reporting(dsn: str, transport: Transport | None = None) -> None:
    # The SDK defaults send frame locals and the request body: the ASGI scope in those frames carries the
    # caller's Authorization header (an sbk_ key never expires), and locals hold OAuth tokens and prospect PII (#429).
    betterstack_sdk.init(
        dsn=dsn,
        transport=transport,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size='never',
        before_send=_drop_query_string,
        before_breadcrumb=_strip_breadcrumb_query,
    )


configure_logging()
if settings.betterstack_api_error_dsn:
    init_error_reporting(settings.betterstack_api_error_dsn)

logger = structlog.get_logger()
app = FastAPI(title='zenbree-api')
install_error_handlers(app)
# Added before CORS so it runs inside it: a browser can read the 413.
app.add_middleware(
    BodyLimit,
    limits={
        '/v1/lists/upload': CSV_MAX_BYTES,
        '/v1/lists/upload/preview': CSV_MAX_BYTES,
        '/v1/me/photo': PHOTO_MAX_BYTES,
    },
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)
app.include_router(linkedin_router)
app.include_router(mailboxes_router)
app.include_router(mailbox_push_router)
app.include_router(hubspot_router)
app.include_router(instantly_router)
app.include_router(instantly_accounts_router)
app.include_router(instantly_stops_router)
app.include_router(instantly_webhook_router)
app.include_router(hubspot_webhooks_router)
app.include_router(member_release_router)
app.include_router(senders_router)
app.include_router(webhooks_router)
app.include_router(webhook_deliveries_router)
app.include_router(api_keys_router)
app.include_router(account_delete_router)
app.include_router(photo_router)

app.include_router(billing_router)
app.include_router(plan_change_router)
app.include_router(billing_profile_router)
app.include_router(billing_invoices_router)
app.include_router(billing_ledger_router)
app.include_router(admin_router)
app.include_router(admin_ban_router)
app.include_router(admin_chats_router)
app.include_router(admin_jobs_router)

app.include_router(agent_router)
app.include_router(session_delete_router)
app.include_router(csv_import_router)  # before lists_router: /imports/... is not a list id
app.include_router(lists_router)
app.include_router(list_export_router)
app.include_router(list_bulk_router)
app.include_router(snippet_router)
app.include_router(row_phone_router)
app.include_router(memory_router)
app.include_router(inbox_router)
app.include_router(unsubscribe_router)
app.include_router(exclusions_router)
app.include_router(views_router)
app.include_router(segments_router)
app.include_router(audience_router)
app.include_router(projects_router)
app.include_router(scheduling_router)
app.include_router(watchlists_router)
app.include_router(automations_router)
app.include_router(automation_hooks_router)
app.include_router(onboarding_router)
app.include_router(campaigns_router)
app.include_router(activity_router)
app.include_router(reports_router)
app.include_router(campaign_senders_router)
app.include_router(campaign_fields_router)
app.include_router(campaign_test_email_router)
app.include_router(campaign_variants_router)
app.include_router(campaign_snippet_rows_router)
app.include_router(home_router)
app.include_router(notifications_router)


@app.middleware('http')
async def _clear_log_context(request: Request, call_next):
    structlog.contextvars.clear_contextvars()
    return await call_next(request)


@cache
def _redis() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)


class Health(BaseModel):
    status: Literal['ok', 'degraded']
    db: str
    redis: str


class Me(BaseModel):
    user_id: str
    email: str | None
    role: str | None
    org_role: str | None
    credits_remaining: int | None
    # the credits the organization's live plan grants each period (#434), the sidebar bar's 100%; null without a plan
    plan_credits: int | None
    # the balance split (billing page): the plan's unused allowance, which resets at `plan_credits_reset_at`,
    # and the credits bought, which do not expire. The split fields are null without a live plan.
    plan_credits_left: int | None
    plan_credits_reset_at: datetime | None
    purchased_credits: int | None


class AdminPing(BaseModel):
    ok: bool
    admin: str


@app.get(
    '/health',
    response_model=Health,
    responses={503: {'model': Health}},
    dependencies=[public(SystemReason.HEALTH)],
)
def health() -> JSONResponse:
    checks: dict[str, str] = {}
    try:
        with get_conn(timeout=2) as conn:
            conn.execute('SELECT 1')
        checks['db'] = 'ok'
    except Exception as e:
        checks['db'] = f'error: {type(e).__name__}'
    try:
        _redis().ping()
        checks['redis'] = 'ok'
    except Exception as e:
        checks['redis'] = f'error: {type(e).__name__}'
    healthy = all(v == 'ok' for v in checks.values())
    if not healthy:
        logger.warning('health.degraded', **checks)
    return JSONResponse({'status': 'ok' if healthy else 'degraded', **checks}, status_code=200 if healthy else 503)


@app.get('/v1/me', response_model=Me)
def me(user: CurrentUser = Depends(current_user_or_api_key)) -> dict:
    plan = _live_subscription(user.org_id)
    # snake_case contract with web/ (its http-client camelCases it)
    return {
        'user_id': user.id,
        'email': user.email,
        'role': user.role,
        'org_role': user.org_role,  # #308: read from `member` with every call
        'plan_credits': next((p.credits for p in settings.billing_plans if plan and p.id == plan['plan_id']), None),
        **credit_split(user.org_id, plan),
    }


@app.get('/v1/admin/ping', response_model=AdminPing)
def admin_ping(user: CurrentUser = Depends(require_admin)) -> dict:
    return {'ok': True, 'admin': user.id}
