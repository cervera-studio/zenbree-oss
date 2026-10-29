"""A plan stage's clock, for the back office (#352): from its first call that passed the preflight to its last
result. A refused call never starts it."""

from datetime import UTC, datetime


def start(stage: dict, retried: bool) -> bool:
    """Starts the clock unless it runs already; a retried stage starts again. True when the stage changed."""
    if retried:
        stage.pop('finished_at', None)
    elif 'started_at' in stage:
        return False
    stage['started_at'] = datetime.now(UTC).isoformat()
    return True


def finish(stage: dict) -> None:
    if 'started_at' in stage:
        stage['finished_at'] = datetime.now(UTC).isoformat()


def progress(stages: list[dict]) -> dict:
    return {
        'progress_done': sum(min(s['done'], s['units']) for s in stages),
        'progress_total': sum(s['units'] for s in stages),
    }
