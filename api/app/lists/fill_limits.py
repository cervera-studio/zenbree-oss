"""What one organization may ask of the AI column fills: the operator pays for the search and the model call of
every cell, filled or not, so attempts are bounded here (#960, #971). Every fill goes through ``queue_fill``, which
calls ``check`` after it claims the column: the route, the refill and the chat plan alike."""

from app.core.rate_limit import over_limit

# ponytail: 10 fills per organization per hour (at most 2000 cells researched); a per-attempt charge or a daily
# attempt quota if abuse outgrows it.
FILLS_PER_HOUR = 10
# ponytail: 3 fills live at once per organization, so one tenant keeps at most 3 of the worker's 4 slots; a
# dedicated queue if it still starves others.
LIVE_FILLS = 3
# ponytail: 20 AI columns per list, a ceiling on what one list can ask the fills for.
COLUMNS_PER_LIST = 20
RATE_LIMITED = 'LIST.AI_FILL_RATE_LIMITED'


class FillLimited(Exception):
    def __init__(self, code: str, retry_after: int) -> None:
        super().__init__(code)
        self.code, self.retry_after = code, retry_after


def check(conn, list_id: str, live_sql: str) -> None:
    """Raise ``FillLimited`` past the live cap or the hourly quota. The caller's transaction rolls the claim back."""
    org = conn.execute('SELECT organization_id FROM lists WHERE id = %s', (list_id,)).fetchone()['organization_id']
    conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (f'ai_fill:{org}',))  # two claims never both count
    live = conn.execute(
        "SELECT count(*) AS n FROM list_columns WHERE kind = 'ai' AND deleted_at IS NULL"
        f' AND list_id IN (SELECT id FROM lists WHERE organization_id = %s) AND {live_sql}',
        (org,),
    ).fetchone()['n']
    if live > LIVE_FILLS:  # the claimed column counts
        raise FillLimited('LIST.AI_FILL_BUSY', 60)
    if over_limit(f'rl:list:ai_fill:{org}', 'ai_fill', FILLS_PER_HOUR, 3600):
        raise FillLimited(RATE_LIMITED, 3600)
