"""The email and phone lookups a chat turn has open (``lookup_claims``): the only code that writes that table.

A handler claims the rows it will search before its first vendor call, so a reader sees them as ``searching``
(``CHARGES_SQL``) for as long as the call runs. The loop releases the claim in the transaction that stores the call's
answers, so no reader sees a row with neither the claim nor the answer."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.agent.jobs import STALE_JOB_MINUTES
from app.core.db import get_conn

if TYPE_CHECKING:  # lookup_outcome reads lookups, which reads this module
    from app.lists.lookup_outcome import Contact

# On ``lookup_claims c``: its job still has its worker. A worker that died leaves its job 'running': its claims stop
# counting when the chat counts the job as lost.
_LIVE_JOB = (
    'EXISTS (SELECT 1 FROM jobs j WHERE j.id = c.job_id'
    f" AND j.status = 'running' AND j.updated_at > now() - interval '{STALE_JOB_MINUTES} minutes')"
)
# On ``list_rows r``: the keys that a live job claims. A claim of a job whose worker died stops counting at once.
CLAIMED_KEYS_SQL = f'ARRAY(SELECT c.key FROM lookup_claims c WHERE c.row_id = r.id AND {_LIVE_JOB})'


def claim(conn, job_id: str, call_id: str, list_id: str, key: Contact, row_ids: Sequence[str]) -> int:
    """Claims ``key`` on the rows of ``list_id`` among ``row_ids`` for the call; a retried claim changes nothing. Returns the rows now claimed."""
    return conn.execute(
        'INSERT INTO lookup_claims (job_id, tool_call_id, row_id, key)'
        ' SELECT %s, %s, id, %s FROM list_rows WHERE list_id = %s AND id = ANY(%s::uuid[]) ON CONFLICT DO NOTHING',
        (job_id, call_id, key, list_id, list(row_ids)),
    ).rowcount


def release(conn, job_id: str, call_id: str) -> None:
    """In the transaction that stores the call's row_updates: the answers replace the claim at once."""
    conn.execute('DELETE FROM lookup_claims WHERE job_id = %s AND tool_call_id = %s', (job_id, call_id))


def release_call(job_id: str, call_id: str) -> None:
    """A call that stored nothing (it failed or crashed) still lets go of its rows."""
    with get_conn() as conn:
        release(conn, job_id, call_id)


def delete_dead(conn) -> int:
    """The claims of every job that is no longer live, which already read as unclaimed. Returns how many."""
    return conn.execute(f'DELETE FROM lookup_claims c WHERE NOT {_LIVE_JOB}').rowcount


@dataclass(frozen=True)
class Claimer:
    """What a tool call claims with: its job and call, and the ``list_updated`` frame that makes the chat panel
    refetch, so the claimed rows pulse while the vendors answer."""

    job_id: str
    call_id: str
    list_id: str | None
    emit: Callable[[str, dict], None]

    def __call__(self, key: Contact, row_ids: Sequence[str]) -> None:
        if not self.list_id:
            return
        with get_conn() as conn:
            claimed = claim(conn, self.job_id, self.call_id, self.list_id, key, row_ids)
        if claimed:  # no entity: the list may be a company list, and the web finds its slot by list id
            self.emit('list_updated', {'list_id': self.list_id, 'entity': None, 'rows_updated': claimed})
