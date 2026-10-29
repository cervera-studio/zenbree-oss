import asyncio

import structlog
from psycopg.types.json import Jsonb

from app.agent.tools.registry import ToolContext, ToolDef, ToolResult, register
from app.core.db import get_conn
from app.outreach.domain.campaign import MAX_STEPS
from app.outreach.drafts import Draft, draft_from_list
from app.outreach.fields import parse_fields
from app.outreach.step_copy import StepsRefused, checked_steps
from app.outreach.variants import has_test

logger = structlog.get_logger()


_STEP_FIELDS = {'channel', 'subject', 'body', 'send_after_days', 'threaded', 'b'}


def _numbered(raw: object) -> list[dict] | str:
    """The model's steps with their numbers, or why they are not a list of steps."""
    if not isinstance(raw, list) or not all(isinstance(s, dict) for s in raw):
        return 'steps must be a list of {channel, subject, body, send_after_days, threaded, b}.'
    if extra := sorted({k for s in raw for k in s} - _STEP_FIELDS):
        return f'steps have unknown fields: {", ".join(extra)}.'
    return [{'step': n, 'subject': '', 'body': '', 'threaded': False, **s} for n, s in enumerate(raw, 1)]


class _Undone(Exception):
    """A refusal after the draft was written: raised through ``get_conn``, which rolls the transaction back. A
    ``conn.rollback()`` would end the transaction that entered the turn's scope, and the next statement on the
    connection would run with none."""

    def __init__(self, error: str) -> None:
        super().__init__(error)
        self.result = ToolResult(ok=False, error=error)


def _draft(ctx: ToolContext, name: str, campaign_id: str | None, steps: list[dict] | None) -> ToolResult:
    try:
        return _write(ctx, name, campaign_id, steps)
    except _Undone as undone:
        return undone.result


def _write(ctx: ToolContext, name: str, campaign_id: str | None, steps: list[dict] | None) -> ToolResult:
    with get_conn() as conn:
        # The chat's own person list: the model cannot name another list, so it cannot reach another chat's.
        lst = conn.execute(
            'SELECT l.id, (SELECT count(*) FROM list_rows r WHERE r.list_id = l.id AND NOT r.excluded) AS n'
            ' FROM lists l JOIN chat_sessions s ON s.id = l.chat_session_id AND s.organization_id = l.organization_id'
            " WHERE s.id = %s AND l.entity = 'person' AND l.deleted_at IS NULL"
            ' ORDER BY l.created_at DESC LIMIT 1',
            (ctx.session_id,),
        ).fetchone()
        if not lst or not lst['n']:
            return ToolResult(ok=False, error='This chat has no people yet. Search for people first.')
        if campaign_id:
            # Only a draft of this chat's list: a campaign of another chat, or one already live, is out of reach.
            row = conn.execute(
                'SELECT id, name, status, (SELECT count(*) FROM outreach_campaign_targets t WHERE t.campaign_id = c.id)'
                ' AS target_count FROM outreach_campaigns c WHERE id::text = %s AND list_id = %s',
                (campaign_id, lst['id']),
            ).fetchone()
            if not row:
                return ToolResult(ok=False, error="campaign_id is not a campaign of this chat's list.")
            if row['status'] != 'draft':
                return ToolResult(
                    ok=False, error=f'That campaign is {row["status"]}: only a draft can be rewritten. Nothing changed.'
                )
            draft = Draft(str(row['id']), row['name'], 0, row['target_count'])
        else:
            draft = draft_from_list(conn, ctx.user_id, str(lst['id']), name, reuse=True)
            if not draft.target_count:
                raise _Undone(
                    'None of the people can be added: a required column still empty, no email or LinkedIn'
                    ' profile, excluded, already contacted, or already in another campaign.'
                )
        saved = conn.execute('SELECT fields, steps FROM outreach_campaigns WHERE id = %s', (draft.id,)).fetchone()
        fields = parse_fields(saved['fields'])
        tokens = [f'{{{{{f.token}}}}}' for f in fields]
        if steps is not None and has_test(saved['steps']) and not has_test(steps):
            raise _Undone(
                'This draft tests a second copy (B) of some steps, and these steps would delete it. Nothing changed:'
                " pass each step's b to keep the test, or tell the user to turn the test off in the campaign builder."
            )
        if steps is not None:
            try:
                steps = checked_steps(steps, agent_copy=True, fields=fields)
            except StepsRefused as e:
                known = f' The draft also has the fields {", ".join(tokens)}.' if tokens else ''
                raise _Undone(f'{e.message}. Fix the steps and call again.{known}') from None
            written = conn.execute(
                "UPDATE outreach_campaigns SET steps = %s WHERE id = %s AND status = 'draft'",
                (Jsonb(steps), draft.id),
            ).rowcount
            if not written:
                raise _Undone('That campaign is no longer a draft. Nothing changed.')
    data = {
        'campaign_id': draft.id,
        'name': draft.name,
        'enrolled': draft.enrolled,
        'target_count': draft.target_count,
        'fields': tokens,
    }
    if steps is not None:
        data['steps_written'] = len(steps)
    return ToolResult(
        ok=True,
        data=data,
        campaign={'id': draft.id, 'name': draft.name, 'target_count': draft.target_count},
    )


async def draft_campaign(args: dict, ctx: ToolContext) -> ToolResult:
    name = str(args.get('name') or '').strip()[:200]
    campaign_id = str(args.get('campaign_id') or '').strip() or None
    if not name and not campaign_id:
        return ToolResult(ok=False, error='name is required.')
    steps = None
    if args.get('steps') is not None:
        numbered = _numbered(args['steps'])
        if isinstance(numbered, str):
            return ToolResult(ok=False, error=numbered)
        steps = numbered
    try:
        return await asyncio.to_thread(_draft, ctx, name, campaign_id, steps)
    except Exception:
        logger.exception('agent.draft_campaign_failed', job_id=ctx.job_id)
        return ToolResult(ok=False, error='The draft could not be created.')


register(
    ToolDef(
        name='draft_campaign',
        description=(
            "Create a campaign draft from this chat's list of people, so the user can review it, and optionally "
            'write its message sequence. Free. Call it once, after the people are saved and their emails are '
            'enriched. Nothing is sent: the user reviews the sequence and activates the campaign. Calling it '
            'again continues the same draft. name: a short campaign name in the language of the conversation. '
            'steps: the whole sequence, which replaces any earlier copy of the draft; write it when the user asks '
            'for copy or a rewrite, and leave it out to keep the copy as it is. Write the copy in the language of '
            'the conversation. Each step is {channel: email|linkedin_connect|linkedin_message|hubspot_task, subject, body, '
            'send_after_days, threaded, b}. send_after_days counts from activation and never decreases. An email '
            'needs a subject, unless threaded is true: then it replies under an earlier email and has no subject. '
            'A LinkedIn step has no subject and is never threaded; a linkedin_message needs an earlier '
            'linkedin_connect. A linkedin_connect body is an optional invite note of at most 300 characters. '
            'b: an optional second copy {subject, body} of a step, for an A/B test; write it only when the user '
            "asks for one or the draft already has one. Some people get b instead of the step's own copy, on the same "
            "channel and day; a threaded email's b has no subject. A draft with b keeps its test only if the "
            'rewrite passes b again. '
            'A hubspot_task step sends nothing: it creates a HubSpot task for the account owner, with the body as '
            'the drafted message and an optional subject; use it only when the user asks for it. '
            'Personalise with {{firstName}}, {{lastName}}, {{companyName}} and {{role}}, and with the fields the user '
            'gave the draft (the result lists them as fields; a new draft has none); use no other brackets or '
            'placeholders. Do not write a sign-off or an opt-out footer: the system adds them when it sends. '
            'To rewrite the draft the user is editing, pass its campaign_id (the user message names it): only a '
            "draft of this chat's list can be written, never an active or paused campaign."
        ),
        input_schema={
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'minLength': 1, 'maxLength': 200},
                'campaign_id': {'type': 'string', 'description': 'An existing draft of this chat to write to.'},
                'steps': {
                    'type': 'array',
                    'maxItems': MAX_STEPS,
                    'items': {
                        'type': 'object',
                        'properties': {
                            'channel': {'enum': ['email', 'linkedin_connect', 'linkedin_message', 'hubspot_task']},
                            'subject': {'type': 'string', 'maxLength': 300},
                            'body': {'type': 'string', 'maxLength': 4000},
                            'send_after_days': {'type': 'integer', 'minimum': 0},
                            'threaded': {'type': 'boolean'},
                            'b': {
                                'type': 'object',
                                'properties': {
                                    'subject': {'type': 'string', 'maxLength': 300},
                                    'body': {'type': 'string', 'maxLength': 4000},
                                },
                                'required': ['body'],
                            },
                        },
                        'required': ['channel', 'body', 'send_after_days'],
                    },
                },
            },
        },
        handler=draft_campaign,
    )
)
