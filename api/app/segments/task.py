"""The Celery beat task of segment member updates (#938), declared here and registered by app/worker.py.

app/worker.py passes ``system_task`` bound to ``SystemReason.DUE_WORK``: the reason stays in the one module that
``SystemReason.ALLOWED`` lists for it.
"""

SOFT_LIMIT_S = 50


def register_segments(due_task) -> None:
    @due_task(name='evaluate_segments', soft_time_limit=SOFT_LIMIT_S, time_limit=SOFT_LIMIT_S + 5)
    def evaluate_segments() -> dict:
        from app.segments.drain import run

        return run()
