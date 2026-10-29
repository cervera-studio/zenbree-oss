"""What the beat tasks of app/worker.py do for automations (#939), as ``app.outreach.campaign_beat`` does for sends."""

import asyncio

from celery import Celery

from app.automations import deliveries, held_writes, instantly_handoff, runner, service, signal_trigger
from app.core import tasks


def dispatch(celery_app: Celery) -> dict:
    """Close dead runs, start the waiting webhook calls and the accounts whose signals reached their minimum, queue the
    due schedules as their creators, and hand every queued run to a worker: a run a call or a signal starts goes to a
    worker in the same tick. Then the Instantly batches of held runs that may go now, and those whose message was
    lost."""
    runner.maintain()
    deliveries.pickup()
    signal_trigger.pickup()
    queued = tasks.queue_as_owners(celery_app, 'run_automation', service.schedule_due())
    return {'queued': queued, 'instantly': instantly_handoff.send(instantly_handoff.pickup())}


def run(run_id: str) -> None:
    asyncio.run(runner.run_automation(run_id))


def write_held(run_id: str) -> None:
    asyncio.run(held_writes.write(run_id))
