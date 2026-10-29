"""The Celery task of an Instantly batch (#929), declared here and registered by app/worker.py (as ``register_sweep``).

It runs as the ``Member`` who confirmed the batch, so a member who left or was banned since stops it at its next
checkout. A batch is resumable by the user (``retry``): a worker killed mid-run leaves it ``running`` until its
heartbeat is stale, and its in-flight people are ``uncertain``.
"""

import asyncio


def register_instantly(member_task) -> None:
    @member_task(name='run_instantly_enrollment', soft_time_limit=900, time_limit=960)
    def run_instantly_enrollment(enrollment_id: str) -> None:
        from app.integrations.instantly_publish import run

        asyncio.run(run(enrollment_id))


def enqueue(enrollment_id: str) -> None:
    """Queues the run, as the member of the request (the task is a ``member_task``)."""
    from app.worker import celery_app

    celery_app.tasks['run_instantly_enrollment'].delay(enrollment_id)
