"""propose_plan (#42 #43): classify the brief, price the run, store a plan version on its list.

The loop pauses the turn when ``needs_approval`` (see app.agent.plan)."""

from __future__ import annotations

import asyncio

from app.agent import plan as plans
from app.agent.tools.registry import ToolContext, ToolDef, ToolResult, register
from app.core.db import get_conn
from app.lists.rows import create_list


def _store(args: dict, plan: dict, ctx: ToolContext) -> dict | str:
    with get_conn() as conn:
        list_id = args.get('list_id')
        if list_id:
            owned = conn.execute(
                'SELECT l.entity FROM lists l'
                ' JOIN chat_sessions s ON s.id = l.chat_session_id AND s.organization_id = l.organization_id'
                ' WHERE l.id::text = %s AND s.id = %s AND l.deleted_at IS NULL',
                (str(list_id), ctx.session_id),
            ).fetchone()
            if not owned:
                return 'list_id is not a list of this chat'
            if owned['entity'] != plan['entity']:
                return f'that list holds {owned["entity"]} rows, not {plan["entity"]}'
        else:
            found = conn.execute(
                'SELECT l.id::text FROM lists l'
                ' JOIN chat_sessions s ON s.id = l.chat_session_id AND s.organization_id = l.organization_id'
                ' WHERE s.id = %s AND l.entity = %s AND l.deleted_at IS NULL ORDER BY l.created_at DESC LIMIT 1',
                (ctx.session_id, plan['entity']),
            ).fetchone()
            list_id = found['id'] if found else None
        if not list_id:
            if plan['mode'] == 'enrich':
                return 'enrich needs the list_id of the list the user already has'
            list_id = create_list(
                conn,
                user_id=ctx.user_id,
                session_id=ctx.session_id,
                name=str(args.get('list_name') or 'List'),
                entity=plan['entity'],
            )
        plans.claim_research_keys(conn, str(list_id), plan)
        if too_many := plans.research_over_cap(conn, str(list_id), plan):
            return too_many
        version, needs_approval = plans.save(conn, str(list_id), plan, job_id=ctx.job_id)
    return {**plan, 'list_id': str(list_id), 'version': version, 'needs_approval': needs_approval}


async def propose_plan(args: dict, ctx: ToolContext) -> ToolResult:
    plan = plans.build(args)
    if isinstance(plan, str):
        return ToolResult(ok=False, error=plan)
    stored = await asyncio.to_thread(_store, args, plan, ctx)
    if isinstance(stored, str):
        return ToolResult(ok=False, error=stored)
    return ToolResult(ok=True, data=stored)


register(
    ToolDef(
        name='propose_plan',
        description=(
            'Plan a list run before spending. Call it alone, before the first paid tool of a new list brief, and '
            'again whenever the user changes the target. mode: "build" finds new rows, "enrich" works on rows the '
            'user already has (pass that list_id). columns: every column the list will have; required=true for '
            'the ones the user cannot use a row without (a row missing one is not delivered and its per-row '
            'credits are refunded). stages: the paid tools you will call with their units (calls for searches, '
            'rows for enrich_email); per_row=true when the units grow with target_rows. The server prices the '
            f'plan. Above {plans.LARGE_RUN_CREDITS} credits, counting what this turn already spent, or for a second '
            'plan in the same turn, the run stops until the user approves it on the plan card, so do not ask for '
            'approval in text. A search_companies stage on AI Ark adds "source": "aiark" (billed '
            f'per row, {plans.AIARK_COMPANY_PAGE} rows a call). Research columns: a column with `research` (a '
            'question about each row, e.g. {"label": "Sells to hospitals", "research": "Does this company sell to '
            'hospitals?"}) is researched on the web per row with a source, 1 credit per filled cell, starting on '
            'the first rows while the search continues; do not add a stage for it. Give each plain column a '
            '"description" of a few words (what it holds). Mark a stage "optional": true when the list is still '
            'useful without it, such as phone lookups next to emails: the user can drop it on the plan card to '
            'fit their balance. Never mark the stage that finds the rows, or one a required column depends on.'
        ),
        input_schema={
            'type': 'object',
            'properties': {
                'mode': {'type': 'string', 'enum': ['build', 'enrich']},
                'entity': {'type': 'string', 'enum': ['company', 'person']},
                'target_rows': {'type': 'integer', 'minimum': 1, 'maximum': plans.MAX_PLAN_ROWS},
                'list_name': {'type': 'string'},
                'list_id': {'type': 'string'},
                'columns': {
                    'type': 'array',
                    'maxItems': plans.MAX_COLUMNS,
                    'items': {
                        'type': 'object',
                        'properties': {
                            'key': {'type': 'string', 'description': 'Row field, e.g. full_name, title, email'},
                            'label': {'type': 'string', 'description': 'Column header shown to the user'},
                            'required': {'type': 'boolean'},
                            'description': {'type': 'string', 'maxLength': plans.MAX_DESCRIPTION_CHARS},
                            'research': {'type': 'string', 'maxLength': 500},
                        },
                    },
                },
                'stages': {
                    'type': 'array',
                    'maxItems': plans.MAX_STAGES,
                    'items': {
                        'type': 'object',
                        'properties': {
                            'tool': {'type': 'string', 'description': 'A paid tool, e.g. search_people'},
                            'label': {'type': 'string', 'description': 'Short stage name, e.g. "Find emails"'},
                            'units': {
                                'type': 'integer',
                                'minimum': 0,
                                'description': 'Calls for a search, rows for enrich_email',
                            },
                            'per_row': {'type': 'boolean'},
                            'optional': {'type': 'boolean'},
                            'source': {'type': 'string'},
                        },
                        'required': ['tool', 'units'],
                    },
                },
            },
            'required': ['mode', 'entity', 'target_rows', 'columns', 'stages'],
        },
        handler=propose_plan,
    )
)
