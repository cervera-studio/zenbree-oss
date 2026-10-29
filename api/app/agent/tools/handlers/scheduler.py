"""Daily/weekly saved searches only; never schedule arbitrary tools or sends."""

import asyncio
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError

from app.agent.tools.registry import ToolContext, ToolDef, ToolResult, register
from app.scheduling import service
from app.scheduling.models import SearchInput


async def schedule(args: dict, ctx: ToolContext) -> ToolResult:
    try:
        body = SearchInput.model_validate({**args, 'list_id': args.get('list_id') or ctx.list_id})
        row = await asyncio.to_thread(service.save, ctx.user_id, body)
    except (ValidationError, HTTPException) as exc:
        return ToolResult(ok=False, error=str(exc))
    return ToolResult(
        ok=True,
        data={'schedule_id': str(row['id']), 'name': row['name'], 'cadence': row['cadence'], 'max_credits_per_run': 0},
    )


async def cancel(args: dict, ctx: ToolContext) -> ToolResult:
    try:
        schedule_id = UUID(str(args.get('schedule_id', '')))
        await asyncio.to_thread(service.toggle, ctx.user_id, schedule_id, enabled=False)
    except ValueError, HTTPException:
        return ToolResult(ok=False, error='Saved search not found')
    return ToolResult(ok=True, data={'schedule_id': str(schedule_id), 'enabled': False})


async def list_schedules(args: dict, ctx: ToolContext) -> ToolResult:
    # what the user created, in the chat's organization (row-level security): the ones cancel_schedule may pause
    rows = [r for r in await asyncio.to_thread(service.list_searches) if str(r['user_id']) == ctx.user_id]
    return ToolResult(
        ok=True,
        data={
            'schedules': [
                {'id': str(r['id']), 'name': r['name'], 'cadence': r['cadence'], 'enabled': r['enabled']} for r in rows
            ]
        },
    )


schedule_schema = SearchInput.model_json_schema()
schedule_schema['required'].remove('list_id')
schedule_schema['properties']['list_id']['description'] = 'Owned list ID; omit to use the current result list.'
schedule_schema['properties']['tool_input']['description'] = (
    'The arguments of the search to repeat, in the input shape of tool_name.'
)


register(
    ToolDef(
        name='schedule_check',
        description=(
            'Only when the user explicitly requests repeated searches: save search_people or search_companies '
            'daily or weekly in UTC. Appends new rows to an owned list; rows are free. '
            'Person searches may add drafts to an owned campaign; never sends or enriches contacts.'
        ),
        input_schema=schedule_schema,
        handler=schedule,
    )
)
register(
    ToolDef(
        name='cancel_schedule',
        description=(
            'Pause one saved search so it stops running; it is kept, not deleted, and the user can turn it '
            'back on. Use it when the user asks to stop a repeated search. schedule_id comes from '
            'list_schedules or from the schedule_check that created it. Free. Returns the id with '
            'enabled=false, or "Saved search not found" for an id the user does not own.'
        ),
        input_schema={'type': 'object', 'properties': {'schedule_id': {'type': 'string'}}, 'required': ['schedule_id']},
        handler=cancel,
    )
)
register(
    ToolDef(
        name='list_schedules',
        description=(
            'List the saved searches this user created: id, name, cadence (daily or weekly) and enabled. Free. Use it '
            'when the user asks what runs on a schedule, or to find the schedule_id for cancel_schedule. '
            'It does not return the search arguments, the target list or past runs.'
        ),
        input_schema={'type': 'object', 'properties': {}},
        handler=list_schedules,
    )
)
