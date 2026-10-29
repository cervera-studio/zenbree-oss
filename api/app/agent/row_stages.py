"""A per-row plan stage's size, when the search delivered fewer rows than approved (#568)."""

from app.agent import plan as plans
from app.agent.tools import registry
from app.core.db import get_conn


def _works_on_rows(tool_name: str) -> bool:
    tool = registry.get(tool_name)
    takes = (tool.input_schema.get('properties') or {}) if tool else {}
    return 'row_ids' in takes or 'people' in takes


def fit(job_id: str, stages: list[dict], searched: dict[int, str | None]) -> None:
    """``searched`` maps each stage called this turn to the list its call worked on. A per-row stage waits for no more
    rows than that list holds now, never fewer than it worked, never more than approved. On the plan's list it keeps the
    plan's units per row; another list (a company plan's email step works on the people list) counts its own rows,
    since the plan's ratio is per company. Refit on every call, before any error is set: rows a later search adds grow
    it back, so a failure after them still leaves the stage retryable."""
    lists = {i: list_id for i, list_id in searched.items() if list_id and _works_on_rows(stages[i]['tool'])}
    if not lists:
        return
    with get_conn() as conn:
        approved = plans.for_job(conn, job_id)
        if not approved:
            return
        rows = {
            list_id: conn.execute(
                'SELECT count(*) AS n FROM list_rows WHERE list_id = %s AND NOT excluded', (list_id,)
            ).fetchone()['n']
            for list_id in set(lists.values())
        }
    plan, approved_rows = approved['plan'], approved['approved_rows']
    kept = [s for s in plan['stages'] if not s.get('dropped')]
    cap = plans.stages_for(plan, approved_rows)
    for i, list_id in lists.items():
        if i < len(kept) and kept[i].get('per_row'):
            own = list_id == approved['list_id']
            fit = plans.stages_for(plan, min(approved_rows, rows[list_id]))[i]['units'] if own else rows[list_id]
            stages[i]['units'] = min(cap[i]['units'], max(stages[i]['done'], fit))
