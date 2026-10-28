"""The Celery beat task of the Instantly reconciliation (#930), declared here and registered by app/worker.py.

app/worker.py passes ``system_task`` bound to ``SystemReason.DUE_WORK``: the reason stays in the one module that
``SystemReason.ALLOWED`` lists for it.
"""

import asyncio


def register_instantly_sync(due_task) -> None:
    @due_task(name='sync_instantly', soft_time_limit=50, time_limit=55)
    def sync_instantly() -> dict:
        from app.integrations.instantly_sync.reconcile import run

        return asyncio.run(run())
