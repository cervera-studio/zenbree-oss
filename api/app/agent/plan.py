"""Plans (#42 #43 #45 #47): what a list run will build and what it may spend.

The model proposes a plan with ``propose_plan`` (mode, entity, target rows,
columns with required flags, paid stages); the server prices it from
``ToolDef.price``, so the estimate never comes from the model. Each proposal is
a new version in ``list_plans``. A turn's first plan is approved at once while
what the turn spent plus the plan stays within LARGE_RUN_CREDITS; any other
plan pauses the turn as ``needs_input`` (the ask_user pause) and the user
approves it with a row count on the next chat message (``plan_approval``);
that turn's paid tools may spend up to the approved estimate. Without an
approved plan a turn may spend up to LARGE_RUN_CREDITS. Ported in spirit from
the earlier implementation ``plan_gate.py`` / ``run_budget.py`` / ``plan_pricing.py``: the gate is the
spend, not an instruction to the model.
"""

from __future__ import annotations

import math
import re

import structlog
from psycopg.types.json import Jsonb

from app.agent.tools import registry
from app.lists.ai_columns import AI_CELL, column_key
from app.lists.fill_limits import COLUMNS_PER_LIST
from app.lists.rows import SCORE_KEY, ensure_columns, is_internal

logger = structlog.get_logger()

LARGE_RUN_CREDITS = 100  # owner decision on #43 (2026-10-01)
MAX_PLAN_ROWS = 2000
MAX_COLUMNS = 40
MAX_STAGES = 10
MAX_STAGE_UNITS = 10_000
# search_companies on AI Ark bills per row: one call is a page of this many rows (AiArkClient default)
AIARK_COMPANY_PAGE = 25
MIN_RESEARCH_CHARS = 3
MAX_DESCRIPTION_CHARS = 120


def _key(value: object) -> str:
    return re.sub(r'[^a-z0-9]+', '_', str(value or '').strip().lower()).strip('_')[:60]


def _int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[call-overload]  # anything else is rejected below
    except TypeError, ValueError:
        return None


def price_stages(stages: list[dict], target_rows: int, research_per_row: float) -> tuple[int, float]:
    """(credits_fixed, credits_per_row) of the stages the run executes: a dropped stage costs nothing."""
    fixed, per_row = 0, float(research_per_row)
    for s in stages:
        if s.get('dropped'):
            continue
        if s['per_row']:
            per_row += s['unit_cost'] * s['units'] / target_rows
        else:
            fixed += s['unit_cost'] * s['units']
    return fixed, per_row


def credits_for(plan: dict, rows: int) -> int:
    """Estimated credits for ``rows`` rows: fixed stages plus per-row stages scaled."""
    return plan['credits_fixed'] + math.ceil(plan['credits_per_row'] * rows - 1e-9)


def dropped_tools(plan: dict) -> frozenset[str]:
    """Tools the user dropped from the approved plan and that no kept stage still uses."""
    # ponytail: a dropped stage whose tool a kept stage still uses is not blocked; the repriced ceiling caps it
    kept = {s['tool'] for s in plan['stages'] if not s.get('dropped')}
    return frozenset(s['tool'] for s in plan['stages'] if s.get('dropped')) - kept


def campaign_declined(conn, session_id: str) -> bool:
    """The newest approved plan of this chat's lists dropped its draft_campaign stage."""
    row = conn.execute(
        'SELECT p.plan FROM list_plans p JOIN lists l ON l.id = p.list_id'
        ' JOIN chat_sessions s ON s.id = l.chat_session_id AND s.organization_id = l.organization_id'
        ' WHERE s.id = %s AND p.approved_at IS NOT NULL'
        ' ORDER BY p.approved_at DESC LIMIT 1',
        (session_id,),
    ).fetchone()
    return bool(row) and 'draft_campaign' in dropped_tools(row['plan'])


def research_keys(plan: dict) -> list[str]:
    return [c['key'] for c in plan['columns'] if c.get('research')]


def tool_budget(plan: dict, rows: int, credits: int) -> int:
    """What the turn's tools may spend: research cells are billed by their own fill task."""
    return credits - math.ceil(plan.get('credits_research_per_row', 0) * rows)


def stages_for(plan: dict, rows: int) -> list[dict]:
    return [
        {
            'tool': s['tool'],
            'label': s['label'],
            'units': math.ceil(s['units'] * rows / plan['target_rows']) if s['per_row'] else s['units'],
            'done': 0,
            'per_row': bool(s['per_row']),
        }
        for s in plan['stages']
        if not s.get('dropped')
    ]


def build(args: dict) -> dict | str:
    """The priced plan, or an error the model can fix."""
    mode, entity = args.get('mode'), args.get('entity')
    if mode not in ('build', 'enrich'):
        return "mode must be 'build' (new list) or 'enrich' (rows the user already has)"
    if entity not in ('company', 'person'):
        return "entity must be 'company' or 'person'"
    rows = _int(args.get('target_rows'))
    if rows is None or not 1 <= rows <= MAX_PLAN_ROWS:
        return f'target_rows must be between 1 and {MAX_PLAN_ROWS}'
    columns: dict[str, dict] = {}
    for c in (args.get('columns') or [])[:MAX_COLUMNS]:
        if not isinstance(c, dict):
            continue
        label, research = str(c.get('label') or '').strip()[:60], str(c.get('research') or '').strip()
        if research:
            # #59: an AI column researched on the web per row, filled by the AI column task
            if len(research) < MIN_RESEARCH_CHARS or not label:
                return f'a research column needs a label and a question of at least {MIN_RESEARCH_CHARS} chars'
            if c.get('required') is True:
                # its cells are filled after the turn, so at the refund every row would look undelivered
                return 'a research column cannot be required'
            key = column_key(label, set(columns))
            columns[key] = {'key': key, 'label': label, 'required': False, 'research': research[:500]}
            continue
        key = _key(c.get('key'))
        if key and key != SCORE_KEY and not is_internal(key):
            columns[key] = {'key': key, 'label': label, 'required': c.get('required') is True}
            if description := str(c.get('description') or '').strip()[:MAX_DESCRIPTION_CHARS]:
                columns[key]['description'] = description
    if sum(1 for c in columns.values() if c.get('research')) > COLUMNS_PER_LIST:
        return f'a plan has at most {COLUMNS_PER_LIST} research columns'
    if not columns:
        return 'columns is required: the columns the list will have, the ones the user needs marked required'
    # pay-if-found like the column fill: priced as if every researched cell is filled
    research_per_row = AI_CELL.credits * sum(1 for c in columns.values() if c.get('research'))
    stages = []
    for s in (args.get('stages') or [])[:MAX_STAGES]:
        tool = registry.get(str(s.get('tool'))) if isinstance(s, dict) else None
        units = _int(s.get('units')) if isinstance(s, dict) else None
        if tool is None or units is None or not 0 <= units <= MAX_STAGE_UNITS:
            return f'each stage needs a known tool and units between 0 and {MAX_STAGE_UNITS}: {s!r}'[:300]
        scales = s.get('per_row') is True
        if tool.name == 'search_companies' and s.get('source') == 'aiark':
            units *= AIARK_COMPANY_PAGE  # calls -> billed rows
        stages.append(
            {
                'tool': tool.name,
                'label': str(s.get('label') or tool.name)[:80],
                'units': units,
                'per_row': scales,
                'unit_cost': tool.price.credits if tool.price else 0,
                'optional': s.get('optional') is True,
            }
        )
    fixed, per_row = price_stages(stages, rows, research_per_row)
    plan = {
        'mode': mode,
        'entity': entity,
        'target_rows': rows,
        'columns': list(columns.values()),
        'stages': stages,
        'credits_fixed': fixed,
        'credits_per_row': per_row,
        'credits_research_per_row': research_per_row,
    }
    return {**plan, 'estimated_credits': credits_for(plan, rows), 'large_run_credits': LARGE_RUN_CREDITS}


def apply_columns(conn, list_id: str, plan: dict) -> None:
    """The plan's columns exist on the list; its required flags replace the old ones (#45)."""
    research = [c for c in plan['columns'] if c.get('research')]
    ensure_columns(conn, list_id, plan['entity'], {c['key'] for c in plan['columns'] if not c.get('research')})
    if research:
        conn.execute('SELECT 1 FROM lists WHERE id = %s FOR UPDATE', (list_id,))  # serialises positions
        taken = {
            r['key']
            for r in conn.execute(
                "SELECT key FROM list_columns WHERE list_id = %s AND kind = 'ai' AND deleted_at IS NULL", (list_id,)
            )
        }
        # a list holds COLUMNS_PER_LIST AI columns: the plan's new ones past that are not created
        new = [c for c in research if c['key'] not in taken]
        dropped = new[max(0, COLUMNS_PER_LIST - len(taken)) :]
        if dropped:
            logger.warning('plan.research_columns_over_cap', list_id=list_id, keys=[c['key'] for c in dropped])
        research = [c for c in research if c not in dropped]
        pos = conn.execute(
            'SELECT coalesce(max(position), -1) AS p FROM list_columns WHERE list_id = %s', (list_id,)
        ).fetchone()['p']
        for i, c in enumerate(research, start=pos + 1):
            # an existing column with the same key keeps its prompt and its cells
            conn.execute(
                "INSERT INTO list_columns (list_id, key, label, kind, position, config) VALUES (%s, %s, %s, 'ai', %s, %s)"
                ' ON CONFLICT DO NOTHING',
                (list_id, c['key'], c['label'], i, Jsonb({'prompt': c['research']})),
            )
    for c in plan['columns']:
        if c['label']:
            conn.execute(
                'UPDATE list_columns SET label = %s WHERE list_id = %s AND key = %s', (c['label'], list_id, c['key'])
            )
    conn.execute(
        # an AI column fills only on delivered rows (#150): required, it would never fill
        "UPDATE list_columns SET config = CASE WHEN key = ANY(%s) AND kind <> 'ai' THEN config || '{\"required\": true}'"
        " ELSE config - 'required' END WHERE list_id = %s",
        ([c['key'] for c in plan['columns'] if c['required'] and not c.get('research')], list_id),
    )


def claim_research_keys(conn, list_id: str, plan: dict) -> None:
    """A research key the list already uses for a non-AI column (a CSV header "AI score") or a deleted one
    (#341) gets a fresh key, so the plan never prices a column its fill would ignore."""
    static = {
        r['key']
        for r in conn.execute(
            "SELECT key FROM list_columns WHERE list_id = %s AND (kind <> 'ai' OR deleted_at IS NOT NULL)", (list_id,)
        )
    }
    taken = static | {c['key'] for c in plan['columns']}
    for c in plan['columns']:
        if c.get('research') and c['key'] in static:
            c['key'] = column_key(c['label'], taken)
            taken.add(c['key'])


def research_over_cap(conn, list_id: str, plan: dict) -> str | None:
    """An error for the model when the plan's new research columns would take the list past COLUMNS_PER_LIST."""
    live = {
        r['key']
        for r in conn.execute(
            "SELECT key FROM list_columns WHERE list_id = %s AND kind = 'ai' AND deleted_at IS NULL", (list_id,)
        )
    }
    new = {c['key'] for c in plan['columns'] if c.get('research')} - live
    if len(live) + len(new) > COLUMNS_PER_LIST:
        return f'the list has {len(live)} AI columns and holds at most {COLUMNS_PER_LIST}: drop research columns'
    return None


def _auto_approves(conn, job_id: str | None, credits: int) -> bool:
    """#408: only the turn's first plan, and only while what the turn recorded plus the plan stays within
    LARGE_RUN_CREDITS. The turn's records include its research fills, but a fill bills after its plan, so each
    further plan would queue its own before the spend shows."""
    if job_id is None:
        return False
    turn = conn.execute(
        "SELECT (SELECT coalesce(sum(credits), 0) FROM row_charges WHERE job_id = j.id AND state <> 'refunded')"
        ' AS spent, EXISTS (SELECT 1 FROM list_plans WHERE approved_job_id = j.id) AS planned'
        ' FROM jobs j WHERE j.id = %s',
        (job_id,),
    ).fetchone()
    return bool(turn) and not turn['planned'] and turn['spent'] + credits <= LARGE_RUN_CREDITS


def save(conn, list_id: str, plan: dict, *, job_id: str | None) -> tuple[int, bool]:
    """A new version, approved for ``job_id`` at once when it auto-approves. Returns (version, needs_approval)."""
    auto = _auto_approves(conn, job_id, plan['estimated_credits'])
    version = conn.execute(
        'INSERT INTO list_plans (list_id, version, plan, approved_at, approved_rows, approved_credits, approved_job_id)'
        ' SELECT %s, coalesce(max(version), 0) + 1, %s, CASE WHEN %s THEN now() END, %s, %s, %s'
        ' FROM list_plans WHERE list_id = %s RETURNING version',
        (
            list_id,
            Jsonb(plan),
            auto,
            plan['target_rows'] if auto else None,
            plan['estimated_credits'] if auto else None,
            job_id if auto else None,
            list_id,
        ),
    ).fetchone()['version']
    conn.execute('UPDATE lists SET mode = %s WHERE id = %s', (plan['mode'], list_id))
    if auto:
        apply_columns(conn, list_id, plan)
    return version, not auto


class PlanNotFound(LookupError):
    pass


class PlanStale(ValueError):
    pass


class StageUnknown(ValueError):
    """The approval drops a stage index the plan does not have."""


class StageRequired(ValueError):
    """The approval drops a stage the model did not mark optional."""


def drop_stages(plan: dict, dropped: list[int]) -> dict:
    """``plan`` with exactly the stages at ``dropped`` marked dropped and its credits repriced.

    Idempotent: the flags are reset on every call, so a second approval of the same version starts clean."""
    if bad := [i for i in dropped if not 0 <= i < len(plan['stages'])]:
        raise StageUnknown(bad)
    if required := [i for i in dropped if not plan['stages'][i].get('optional')]:
        raise StageRequired(required)
    stages = [{**s, 'dropped': i in dropped} for i, s in enumerate(plan['stages'])]
    if not all('unit_cost' in s for s in stages):
        # ponytail: plans proposed before #212 have no unit_cost; the API never loads the tools, so the stored ceiling stands
        return {**plan, 'stages': stages}
    fixed, per_row = price_stages(stages, plan['target_rows'], plan.get('credits_research_per_row', 0))
    planned = {**plan, 'stages': stages, 'credits_fixed': fixed, 'credits_per_row': per_row}
    return {**planned, 'estimated_credits': credits_for(planned, plan['target_rows'])}


def approve(
    conn, *, org_id: str, session_id: str, list_id: str, version: int, rows: int, job_id: str, dropped: list[int]
) -> None:
    """The user approved the latest plan of their organization's list in this chat for ``rows`` rows without the stages at
    ``dropped``; ``job_id`` runs it. The plan is repriced here: the client's total is never trusted."""
    row = conn.execute(
        'SELECT p.plan, (SELECT max(version) FROM list_plans WHERE list_id = p.list_id) AS latest'
        ' FROM list_plans p JOIN lists l ON l.id = p.list_id WHERE p.list_id = %s AND p.version = %s'
        ' AND l.organization_id = %s AND l.chat_session_id = %s AND l.deleted_at IS NULL',
        (list_id, version, org_id, session_id),
    ).fetchone()
    if not row:
        raise PlanNotFound
    if row['latest'] != version:
        raise PlanStale
    plan = drop_stages(row['plan'], dropped)
    conn.execute(
        'UPDATE list_plans SET plan = %s, approved_at = now(), approved_rows = %s, approved_credits = %s,'
        ' approved_job_id = %s WHERE list_id = %s AND version = %s',
        (Jsonb(plan), rows, credits_for(plan, rows), job_id, list_id, version),
    )
    apply_columns(conn, list_id, plan)
    shown = {
        'stages': plan['stages'],
        'credits_fixed': plan['credits_fixed'],
        'credits_per_row': plan['credits_per_row'],
        'estimated_credits': plan['estimated_credits'],
        'approved_rows': rows,
    }
    conn.execute(
        "UPDATE chat_messages SET question = jsonb_set(question, '{plan}', (question->'plan') || %s)"
        " WHERE session_id = %s AND question->'plan'->>'list_id' = %s AND (question->'plan'->>'version')::int = %s",
        (Jsonb(shown), session_id, list_id, version),
    )


def for_job(conn, job_id: str) -> dict | None:
    """The plan approved for this turn: {list_id, version, plan, approved_rows, approved_credits}."""
    return conn.execute(
        'SELECT list_id::text, version, plan, approved_rows, approved_credits FROM list_plans'
        ' WHERE approved_job_id = %s ORDER BY approved_at DESC LIMIT 1',
        (job_id,),
    ).fetchone()


def prompt_section(conn, list_ids: list[str], job_id: str) -> str:
    """The latest plan of each list, and whether this turn runs it."""
    lines = []
    for r in conn.execute(
        'SELECT DISTINCT ON (list_id) list_id::text, version, plan, approved_job_id::text AS job, approved_rows,'
        ' approved_credits FROM list_plans WHERE list_id = ANY(%s::uuid[]) ORDER BY list_id, version DESC',
        (list_ids,),
    ):
        p = r['plan']
        required = [c['key'] for c in p['columns'] if c['required']] or ['none']
        skipped = [s['label'] for s in p['stages'] if s.get('dropped')]
        if r['job'] == job_id:
            state = f'APPROVED for this run: {r["approved_rows"]} rows, at most {r["approved_credits"]} credits. Execute it.'
            if skipped:
                state += f' The user dropped these stages, do not run them: {", ".join(skipped)}.'
        elif r['job']:
            state = 'approved for an earlier run'
        else:
            state = 'waiting for the user to approve it on the plan card'
        lines.append(
            f'- list {r["list_id"]} plan v{r["version"]} ({p["mode"]}, {p["entity"]}, {p["target_rows"]} rows,'
            f' ~{p["estimated_credits"]} credits; required: {", ".join(required)}): {state}'
        )
    return 'Plans:\n' + '\n'.join(lines) if lines else ''
