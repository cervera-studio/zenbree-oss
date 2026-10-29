"""Celery app + beat schedule. Same image as the api; run with
``celery -A app.worker worker|beat|flower``.
Janitors ported from API ``app/billing/worker_tasks.py`` (pg_cron → beat).
"""

from functools import partial

import psycopg
import structlog
from celery import Celery
from celery.schedules import crontab

from app.auth.photo_sweep import register_sweep
from app.core import tasks
from app.core.celery_logging import configure_celery_logging
from app.core.config import settings
from app.core.db import get_conn
from app.core.system_reasons import SystemReason
from app.integrations.instantly_sync.task import register_instantly_sync
from app.integrations.instantly_task import register_instantly
from app.integrations.mailbox_push import register as register_push
from app.lists.csv_task import register_csv_import
from app.segments.task import register_segments

configure_celery_logging()
logger = structlog.get_logger()

celery_app = Celery('zenbree', broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    # Redis redelivers an unacked task after visibility_timeout: keep it longer than the longest job, or it runs twice.
    # Socket timeouts: an enqueue on a hung Redis (app.billing.balance.changed) fails fast instead of
    # holding up the request or job that queued it.
    broker_transport_options={'visibility_timeout': 7200, 'socket_timeout': 5, 'socket_connect_timeout': 5},
    task_acks_late=True,
    task_routes={'deliver_webhooks': {'queue': 'webhooks'}},
    worker_prefetch_multiplier=1,
    timezone='UTC',
    beat_scheduler='redbeat.RedBeatScheduler',
    redbeat_redis_url=settings.redis_url,
    beat_schedule={
        'dispatch-saved-searches': {'task': 'dispatch_saved_searches', 'schedule': 60.0},
        'dispatch-campaign-sends': {'task': 'dispatch_campaign_sends', 'schedule': 60.0},
        'dispatch-watchlists': {'task': 'dispatch_watchlists', 'schedule': 60.0},
        'dispatch-automations': {'task': 'dispatch_automations', 'schedule': 60.0},
        'link-audience-rows': {'task': 'link_audience_rows', 'schedule': 30.0},
        'evaluate-segments': {'task': 'evaluate_segments', 'schedule': 30.0},
        'reap-csv-imports': {'task': 'reap_csv_imports', 'schedule': 300.0},
        'sync-hubspot-contacts': {'task': 'sync_hubspot_contacts', 'schedule': 300.0},
        'sync-instantly': {'task': 'sync_instantly', 'schedule': 60.0},
        'sync-billing-customer-emails': {'task': 'sync_billing_customer_emails', 'schedule': 600.0},
        'reap-stale-reservations': {'task': 'reap_stale_reservations', 'schedule': 60.0},
        'commit-settling-reservations': {'task': 'commit_settling_reservations', 'schedule': 60.0},
        # hourly: send_notification_digests sends to the users whose digest schedule is this hour
        'notification-digests': {'task': 'send_notification_digests', 'schedule': crontab(minute=0)},
        'llm-usage-ttl-cleanup': {'task': 'llm_usage_ttl_cleanup', 'schedule': crontab(hour=3, minute=0)},
        'sweep-profile-photos': {'task': 'sweep_profile_photos', 'schedule': crontab(hour=3, minute=30)},
    },
)
app = celery_app  # what `celery -A app.worker` looks up
member_task, org_task = partial(tasks.member_task, celery_app), partial(tasks.org_task, celery_app)
system_task = partial(tasks.system_task, celery_app)
register_sweep(system_task)
register_push(org_task)
register_instantly(member_task)
register_instantly_sync(partial(system_task, SystemReason.DUE_WORK))
register_segments(partial(system_task, SystemReason.DUE_WORK))
run_csv_import = register_csv_import(member_task)  # the task, for `.delay` callers

#: How long a hold may stay ``reserved`` before the sweep decides the process that took it is gone. Must outlast the
#: longest run, run_job's 960 s hard limit: a hold is settled when the run ENDS. A stale hold keeps what its
#: charges cost (``settle_hold``, #415), and its held charges become final, so a turn's pending cells show; a shorter
#: threshold closes a live run.
RESERVATION_STALE_AFTER_MINUTES = 20


@system_task(SystemReason.RESERVATION_REAPER, name='reap_stale_reservations', soft_time_limit=10, time_limit=15)
def reap_stale_reservations() -> dict:
    from app.billing import balance
    from app.lists import lookup_claims

    with get_conn() as conn:
        rows = conn.execute(
            'SELECT r.id, t.organization_id FROM reap_stale_reservations_ids(%s) AS r(id)'
            ' JOIN credit_transactions t ON t.id = r.id',
            (RESERVATION_STALE_AFTER_MINUTES,),
        ).fetchall()
        lookup_claims.delete_dead(conn)
    ids = [str(r['id']) for r in rows]
    if ids:
        logger.warning('credit.reservations_reaped', count=len(ids), reservation_ids=ids)
    for org_id in {r['organization_id'] for r in rows}:
        balance.changed(org_id)
    return {'released': len(ids)}


@system_task(SystemReason.RESERVATION_REAPER, name='commit_settling_reservations', soft_time_limit=10, time_limit=15)
def commit_settling_reservations() -> dict:
    # Holds a caller moved to 'settling' after its work landed.
    with get_conn() as conn:
        committed = conn.execute('SELECT commit_settling_reservations(5) AS n').fetchone()['n']
    if committed:
        logger.warning('credit.settling_reservations_committed', count=committed)
    return {'committed': committed}


# Queued by app.billing.balance.changed after each committed balance change (#243). A burst of changes
# queues a burst of these: the auto top-up claim and the low-credit disarm are each one conditional
# UPDATE, so at most one charge and one notice come out of it, and a retry repeats nothing.
@org_task(
    name='on_balance_changed',
    ignore_result=True,  # nothing reads it, and recording it makes the enqueue wait on the result backend
    autoretry_for=(psycopg.OperationalError,),
    max_retries=3,
    retry_backoff=2,
    soft_time_limit=10,
    time_limit=15,
)
def on_balance_changed(org_id: str, refused: bool = False) -> dict:
    from app.billing.auto_topup import claim_due
    from app.notifications.low_credits import low_credits
    from app.notifications.service import deliver

    due = claim_due(org_id, refused=refused)
    for r in due:
        auto_topup_charge.delay(r['organization_id'], r['pack_id'], r['charging_since'].isoformat())
    with get_conn() as conn:  # disarm committed before any email: a slow send never re-sends
        notified = low_credits(conn, org_id)
    for n in notified:
        deliver(n)
    return {'charged': len(due), 'notified': bool(notified)}


# Over Stripe's 80s request timeout: a slow Stripe ends as SoftTimeLimitExceeded inside charge(), which
# discards the invoice and turns auto top-up off instead of leaving the claim set.
# #499: invoices follow a changed login email. A Stripe call per stale customer, so the limits leave room for
# a whole batch on a slow day; an overlapping run only repeats an idempotent update.
@system_task(SystemReason.BILLING_EMAIL_SYNC, name='sync_billing_customer_emails', soft_time_limit=240, time_limit=270)
def sync_billing_customer_emails() -> dict:
    from app.billing.customer_email import sync

    return sync()


@org_task(name='auto_topup_charge', soft_time_limit=150, time_limit=180)
def auto_topup_charge(org_id: str, pack_id: str, claim: str) -> str:
    from app.billing.auto_topup import charge

    return charge(org_id, pack_id, claim)


@system_task(SystemReason.RETENTION, name='llm_usage_ttl_cleanup', soft_time_limit=60, time_limit=90)
def llm_usage_ttl_cleanup() -> dict:
    from app.core.retention import purge

    return purge()


# ── feature/m1-agent ───────────────────────────────────────────────────────
# Hard limit well under RESERVATION_STALE_AFTER_MINUTES: the hold is settled
# when the run ends, and the reaper must never beat a live run to it.
@member_task(name='run_job', soft_time_limit=900, time_limit=960)
def run_job(job_id: str) -> None:
    import asyncio

    import app.agent.tools.handlers  # noqa: F401 — registers every tool
    from app.agent.loop import run_job as run

    asyncio.run(run(job_id))


@system_task(SystemReason.DUE_WORK, name='dispatch_saved_searches', soft_time_limit=50, time_limit=55)
def dispatch_saved_searches():
    from app.scheduling.runner import maintain
    from app.scheduling.service import schedule_due

    maintain()
    return {'queued': tasks.queue_as_owners(celery_app, 'run_saved_search', schedule_due())}


@member_task(name='run_saved_search', soft_time_limit=900, time_limit=960)
def run_saved_search(run_id: str):
    import asyncio

    import app.agent.tools.handlers  # noqa: F401
    from app.scheduling.runner import run_search

    asyncio.run(run_search(run_id))


# ── watchlists (E13.3, #647): the same two steps as saved searches. A run reads up to 300 accounts, five at a
# time, so it gets the long limits; its hold is settled when it ends, before the reaper.
@system_task(SystemReason.DUE_WORK, name='dispatch_watchlists', soft_time_limit=50, time_limit=55)
def dispatch_watchlists():
    from app.watchlists.runner import maintain
    from app.watchlists.service import schedule_due

    maintain()
    return {'queued': tasks.queue_as_owners(celery_app, 'run_watchlist', schedule_due())}


@member_task(name='run_watchlist', soft_time_limit=900, time_limit=960)
def run_watchlist(run_id: str):
    import asyncio

    from app.watchlists.runner import run_watchlist as run

    asyncio.run(run(run_id))


# ── automations (#939): a run has the long limits, so its hold is settled before the reaper does it
@system_task(SystemReason.DUE_WORK, name='dispatch_automations', soft_time_limit=50, time_limit=55)
def dispatch_automations():
    from app.automations.beat import dispatch

    return dispatch(celery_app)


@member_task(name='run_automation', soft_time_limit=900, time_limit=960)
def run_automation(run_id: str):
    from app.automations.beat import run

    run(run_id)


@member_task(name='write_held_hubspot', soft_time_limit=600, time_limit=660)
def write_held_hubspot(run_id: str):
    from app.automations.beat import write_held

    write_held(run_id)


@system_task(SystemReason.DUE_WORK, name='link_audience_rows', soft_time_limit=50, time_limit=55)
def link_audience_rows() -> dict:
    from app.audience.beat import run

    return run()  # #937: links the rows that writers marked, an organization at a time


# ── AI columns (E3) ────────────────────────────────────────────────────────
# Same limits as run_job: the per-run hold must be settled before the reaper.
@member_task(name='fill_plan_columns', bind=True, max_retries=30, soft_time_limit=30, time_limit=40)
def fill_plan_columns(self, list_id: str, keys: list[str], cap: int, job_id: str | None = None) -> None:
    """#59: fill a plan's research columns with the AI column task, at most ``cap`` filled cells
    per column (the approved rows). A column whose fill is still live is retried a minute later,
    so rows added meanwhile are researched too. Each fill's charges name ``job_id``, the turn that asked (#408)."""
    from app.core.scope import current
    from app.lists.plan_fills import queue_plan_fills

    by = getattr(current(), 'user', None)  # the member who started the turn spends it
    queued, refused = queue_plan_fills(list_id, keys, cap, by, job_id)
    with get_conn() as conn:
        # not queued: a live fill holds it, the organization's live cap held it, or the user deleted the column
        busy = [
            r['key']
            for r in conn.execute(
                "SELECT key FROM list_columns WHERE list_id = %s AND kind = 'ai' AND deleted_at IS NULL AND key = ANY(%s)",
                (list_id, [k for k in keys if k not in queued and k not in refused]),
            )
        ]
    for key in queued:
        fill_ai_column.delay(list_id, key)
    if busy and self.request.retries >= self.max_retries:
        logger.warning('ai_column.plan_fill_gave_up', list_id=list_id, keys=busy, retries=self.request.retries)
    elif busy:
        raise self.retry(args=(list_id, busy, cap, job_id), countdown=60)


@member_task(name='fill_ai_column', soft_time_limit=900, time_limit=960)
def fill_ai_column(list_id: str, key: str) -> None:
    import asyncio

    import app.agent.tools.handlers  # noqa: F401 — registers web_search
    from app.lists.ai_columns import fill_column

    asyncio.run(fill_column(list_id, key))


@system_task(SystemReason.CSV_REAPER, name='reap_csv_imports', soft_time_limit=50, time_limit=55)
def reap_csv_imports() -> int:
    from app.lists.csv_run import reap

    return reap()


# ── E7 campaign sending (#80, #81) ─────────────────────────────────────────
# An overlapping run is safe: every send takes its mailbox slot by compare-and-set. A run killed
# mid-send leaves that row `sending`, which is never retried automatically.
@system_task(SystemReason.DUE_WORK, name='dispatch_campaign_sends', soft_time_limit=50, time_limit=55)
def dispatch_campaign_sends() -> dict:
    from app.outreach.campaign_beat import run

    return run()


# ── E8 replies (#88, #253): queued by replies.record once the reply is committed ──
@org_task(name='label_reply', bind=True, soft_time_limit=50, time_limit=55)
def label_reply(self, message_id: str) -> str | None:
    import asyncio

    from app.outreach.replies import LABEL_ATTEMPTS, label

    try:
        labelled = asyncio.run(label(message_id))
    except Exception as exc:
        # not the exception text: a provider error can echo the reply
        logger.warning(
            'reply.label_failed', message_id=message_id, attempt=self.request.retries + 1, error_type=type(exc).__name__
        )
        if self.request.retries + 1 >= LABEL_ATTEMPTS:
            return None  # stays unlabelled; the owner labels it in the inbox
        raise self.retry(countdown=60 * 2**self.request.retries) from None
    if labelled:
        try:
            draft_reply.delay(message_id)
        except Exception as exc:  # the label stands; the owner writes the answer
            logger.warning('reply.draft_enqueue_failed', message_id=message_id, error_type=type(exc).__name__)
    return labelled


# #187: the agent's proposed answer, shown in the inbox's reply box. Not retried: without it the box is empty.
@org_task(name='draft_reply', soft_time_limit=50, time_limit=55)
def draft_reply(message_id: str) -> bool:
    import asyncio

    from app.outreach.inbox_reply import draft

    try:
        return asyncio.run(draft(message_id))
    except Exception as exc:  # not the exception text: a provider error can echo the reply
        logger.warning('reply.draft_failed', message_id=message_id, error_type=type(exc).__name__)
        return False


# ── E14.7 notifications (#173) ─────────────────────────────────────────────
# ponytail: ~0.6 s per user, so about 450 digests fit the time limit; batch via Resend's batch API past that.
@system_task(SystemReason.DIGESTS, name='send_notification_digests', soft_time_limit=300, time_limit=330)
def send_notification_digests() -> dict:
    from app.notifications.digest_run import run

    return run()


# #120: a HubSpot webhook delivery. A transient failed read is retried from the queue (1, 2, 4… minutes,
# at least HubSpot's Retry-After), so an unblock or a new block is not lost to a blip; a refused grant
# is not retried (gate_recheck.recheck marks the connection error). HubSpot's own retries cover the route.
@system_task(
    SystemReason.HUBSPOT_WEBHOOK,
    name='apply_hubspot_events',
    bind=True,
    max_retries=6,
    soft_time_limit=120,
    time_limit=150,
)
def apply_hubspot_events(self, events: list[dict]) -> dict:
    import asyncio
    from datetime import UTC, datetime

    from app.crm import gate, gate_recheck
    from app.outreach.dispatcher import _access_token

    try:
        return dict(asyncio.run(gate_recheck.recheck(events, datetime.now(UTC), _access_token)))
    except gate.ReadFailed as exc:
        raise self.retry(exc=exc, countdown=gate.retry_delay(self.request.retries, exc.retry_after)) from exc


# #120: the retry of a gate read that failed in one organization's beat, as its Member(org, owner), who reads nothing
# once gone. The beat leaves the owner alone until this task ran; it re-queues itself with a growing countdown.
@system_task(
    SystemReason.HUBSPOT_WEBHOOK,
    name='retry_hubspot_read',
    bind=True,
    max_retries=8,
    soft_time_limit=120,
    time_limit=150,
)
def retry_hubspot_read(self, owner_id: str, org_id: str) -> dict:
    from app.crm.sync_beat import retry_read

    return retry_read(self, owner_id, org_id)


# #527, #528: people a campaign wrote to become HubSpot contacts, and each send and reply an activity on their
# timeline, for users with write-back on. A sweep, so a send never waits on HubSpot; what it could not write stays
# due and is taken again. One run at a time is enough: an overlap would only find the same contact already there.
@system_task(SystemReason.HUBSPOT_WEBHOOK, name='sync_hubspot_contacts', soft_time_limit=240, time_limit=270)
def sync_hubspot_contacts() -> dict:
    from app.crm.sync_beat import run

    return run()


# ── E9.3 webhooks (#93): one task per endpoint, queued by app.integrations.webhooks.emit, by itself for a retry,
# and by the test route (which alone reads the result). Own queue and worker (compose `worker-webhooks`): a dead or
# slow receiver never takes a slot from run_job or the dispatcher. The soft limit is the POST's total deadline.
@org_task(name='deliver_webhooks', ignore_result=True, soft_time_limit=30, time_limit=45)  # 30 = DELIVERY_DEADLINE_S
def deliver_webhooks(org_id: str, webhook_id: str, payload: dict, attempt: int = 0) -> int | None:
    from app.integrations.webhooks import fire

    return fire(org_id, webhook_id, payload, attempt)
