"""Agent loop: one chat turn = one job (Celery ``run_job``).

model → tool calls → registry handlers → results back → … until the model
answers without tools or MAX_STEPS. Around it: one credit hold per job
(reserve → settle with slack), rows into the session's List, a relevance-score
pass, the assistant message persisted, SSE frames via ``emit``.

Rewritten from scratch; the earlier implementation's loop (loop_runner/turn_executor/tool_dispatcher)
is 17k LOC of guards this product does not have yet.
"""

from __future__ import annotations

import asyncio
import contextvars
import copy
import json
from collections import Counter
from collections.abc import Callable
from urllib.parse import urlparse
from uuid import UUID

import structlog
from psycopg.types.json import Jsonb

from app.admin.settings import vendor_disabled_now
from app.agent import events, free_rows, funnel, retry, row_stages, stages, turn_scope, untrusted
from app.agent import plan as plans
from app.agent.hosts import ROW_HOST_KEYS, Named, host_of
from app.agent.language import DEFAULT_LOCALE, Locale, language_instruction
from app.agent.list_state import list_lines
from app.agent.step_text import FAILED_SEARCH_RULE, STEP_ENDING_TOOLS, Answer, StepText
from app.agent.summary import searched_of, summary_of, with_refund
from app.agent.tools import registry
from app.agent.tools.handlers.search_companies import discover_companies
from app.agent.tools.handlers.search_people import discover_people
from app.agent.tools.registry import ToolContext, ToolResult
from app.agent.turn_credits import TurnCredits
from app.agent.turn_input import latest_ask, model_history, turn_locale, with_brief
from app.agent.units import PricedCall
from app.agent.vendors import tracked as track_vendors
from app.billing.paid_call import current_user_id
from app.billing.tab import HoldGone
from app.core.db import get_conn
from app.core.errors import is_err
from app.core.scope import ActorGone
from app.exclusions.rules import is_excluded, load_rules
from app.integrations import webhooks
from app.lists.ai_research import ungrounded_numbers
from app.lists.delivery import deliver
from app.lists.last_search import remember_result
from app.lists.lookup_claims import Claimer, release, release_call
from app.lists.rows import VENDOR_KEYS, add_rows, create_list, is_internal
from app.llm.base import LLMProvider, ToolCall
from app.llm.json_output import json_schema
from app.llm.metering import MeteringContext, set_metering_context
from app.memory import store as memory_store
from app.memory.document import prompt_section
from app.notifications import service as notifications
from app.onboarding.study import profile_prompt
from app.people.apollo_client import ApolloClient

logger = structlog.get_logger()

Emit = Callable[[str, dict], None]

MAX_STEPS = 12
TURN_DEADLINE_SECONDS = 840
PREVIEW_CHARS = 2000
MODEL_RESULT_CHARS = 12_000
SCORE_BATCH = 25
SCORE_MAX_BATCHES = 20
# Why a row scored what it did (#159): a few short matches to the brief, each tagged
# with the kind of evidence and, when the row's data says, how recent it is.
REASON_CATEGORIES = ('firmographics', 'website', 'hiring', 'news', 'technology', 'contact', 'other')
MAX_REASONS = 3
REASON_CHARS = 160
SOURCE_KIND = {'web_search': 'web', 'fetch_page': 'scrape'}
PARALLEL_TOOL_ALLOWLIST = frozenset({'search_companies', 'search_people'})
MAX_PARALLEL_TOOLS = 4

SYSTEM_PROMPT = """You are Zenbree, a B2B prospecting agent. The user describes who they want to reach; \
you build a List of matching companies or people with your tools.
- Use the search tools to find real entities. Never invent companies, people, emails or URLs.
- Never weaken the user's filters or include weak matches just to reach a requested row count. If the List is short, state exactly how many rows are missing and explain why only when tool output supports the reason; never invent a cause or claim a source is exhausted while more results are available.
- Rows returned by tools are saved to the user's List automatically; do not repeat them in full. \
Summarise what you found (how many, notable examples, gaps) and suggest a next step.
- Apollo is the first source for companies. When a company search result has `local_fallback`, the B2B database is short: \
if the target is local businesses (shops, restaurants, studios, trades, small firms), run that Google Maps search \
right away; otherwise offer it as the next step.
- Large plans wait for the user's approval on the plan card; once approved, execute the plan (see Plans in \
<chat_state>) without asking again.
- To enrich existing rows pass their row ids: from the `list` field of earlier tool results, or find_rows for names.
- Answer a question about what a List already holds (how many rows, emails, verified emails) from <chat_state> or \
find_rows, never with a paid tool: enrich only when the user asks to find what is missing.
- Speak the user's language, not the code's: never show tool names, field names or status values \
(more_rows, catch_all, not_found, row_ids...). Say what they mean: "add more rows", \
"the domain accepts any address, so it can't be verified", "no email found".
- Call tools without announcing them: no text before or between tool calls (the UI already shows each tool). Write only the final answer. Be concise."""

CAMPAIGN_GOAL_PROMPT = """This chat's goal is a campaign draft. Build a List of PEOPLE with emails, not companies. \
In propose_plan add a last stage {"tool": "draft_campaign", "units": 1, "optional": true}. \
When the people are saved and their emails are enriched, call draft_campaign once. \
Then tell the user the draft is ready to review and that nothing is sent until they write the sequence and activate it."""

CAMPAIGN_DECLINED_PROMPT = """The user declined the campaign draft for this chat. \
Do not offer it unless the user asks again with a new plan."""


def _goal(conn, session_id: str, goal: str | None) -> tuple[str, frozenset[str]]:
    """The chat goal's system prompt paragraph and the tools it blocks. A draft the user dropped from an
    approved plan stays declined in every later turn, not only in the turn that ran that plan."""
    if goal != 'campaign':
        return '', frozenset()
    if plans.campaign_declined(conn, session_id):
        return CAMPAIGN_DECLINED_PROMPT, frozenset({'draft_campaign'})
    return CAMPAIGN_GOAL_PROMPT, frozenset()


def _parallelizable(calls: list[ToolCall]) -> bool:
    if len(calls) < 2 or not all(call.name in PARALLEL_TOOL_ALLOWLIST and not call.error for call in calls):
        return False
    identities = [json.dumps([call.name, call.input or {}], sort_keys=True, default=str) for call in calls]
    if len(set(identities)) != len(calls):
        return False
    # AI Ark company search charges per row, so its result may cost more than
    # the one-unit preflight estimate. The local_query branch always uses Serper
    # and caps billing at one unit, regardless of the source field.
    return not any(
        call.name == 'search_companies'
        and not (call.input or {}).get('local_query')
        and (call.input or {}).get('source') == 'aiark'
        for call in calls
    )


def _score_cells(cells: dict) -> dict:
    """What the scorer sees: no vendor names or vendor ids (``apollo_id``…), so none
    can end up in a reason, and no cell a turn may still refund (#416)."""
    hidden = VENDOR_KEYS | registry.billed_keys()
    return {k: v for k, v in cells.items() if k not in hidden and not is_internal(k)}


def _jsonb_or_null(value: object) -> Jsonb | None:
    return None if value is None else Jsonb(value)  # SQL NULL, not JSON null


def clean_reasons(raw: object, cells: dict) -> list[dict] | None:
    """The scorer's reasons, kept only where they hold: a known category, a ``key``
    that is a real, shown field of the row (its ``cell_meta`` source, if any, is
    the link the web shows), and no figure the row's data does not contain.
    None when nothing survives, so the web shows the bare score.

    ponytail: only figures are grounded; a figure-free claim ("expanding to Germany")
    passes on the model's word. Check claims against the cited cell if that bites."""
    if not isinstance(raw, list):
        return None
    evidence = json.dumps(_score_cells(cells), default=str)
    out = []
    for r in raw[:MAX_REASONS]:
        if not isinstance(r, dict) or r.get('category') not in REASON_CATEGORIES:
            continue
        match = str(r.get('match') or '').strip()[:REASON_CHARS]
        if not match or ungrounded_numbers(match, evidence):
            continue
        fresh = str(r.get('freshness') or '').strip()[:40] or None
        key = r.get('key')
        out.append(
            {
                'match': match,
                'category': r['category'],
                'freshness': fresh if fresh and not ungrounded_numbers(fresh, evidence) else None,
                'key': key
                if isinstance(key, str) and key in cells and key not in VENDOR_KEYS and not is_internal(key)
                else None,
            }
        )
    return out or None


class JobCancelled(Exception):
    """The user stopped the job (routes.cancel_job)."""


class Turn:
    def __init__(self, job: dict, provider: LLMProvider, emit: Emit) -> None:
        self.job_id = str(job['id'])
        self.user_id, self.org_id = str(job['user_id']), str(job['organization_id'])
        self.session_id = str(job['chat_session_id'])
        self.project_id: str | None = None  # the chat's project (#307): its profile, exclusions and senders
        self.provider = provider
        self.emit = emit
        self.lists: dict[str, str] = {}  # entity -> list_id
        self.grown: dict[str, str] = {}  # list_id -> entity, lists this turn added rows to
        self.current_list: str | None = None
        self.credits = TurnCredits(self.job_id, self.user_id, self.session_id)
        self.free_rows_holds: dict[str, str] = {}  # call id -> its free-row hold, settled when it finishes
        self.answer = Answer(emit)
        self.tool_calls: list[dict] = []
        self.sources: dict[str, dict] = {}
        self.named = Named()  # #407, #437: the sites fetch_page may read
        self.foreign_history = False  # #963: a teammate's message is in the chat
        self.terminal_code: str | None = None
        self.question: dict | None = None  # ask_user: the turn pauses as needs_input
        self.pick = job.get('pick')  # {tool, args} of the funnel alternative the user picked
        # (entity, filters) of the turn's first Apollo search that found nothing
        self.empty_search: tuple[str, dict] | None = None
        self.local_pending = False  # that search offered a Google Maps fallback which has not come back empty
        self.funnel_tried = False
        self.found_rows = False
        self.brief = self.goal_prompt = self.profile_prompt = ''
        self.list_name = ''
        self.locale: Locale = DEFAULT_LOCALE
        self.score_batches: dict[str, int] = {}
        self.score_last_seq: dict[str, int] = {}
        # #43: paid tools may spend this much; an approved plan raises it to its estimate
        self.budget = plans.LARGE_RUN_CREDITS
        self.planned = False
        self.stages: list[dict] = []  # #49: the plan's stages with progress, a failed one carries `error`
        self.stage_lists: dict[int, str | None] = {}
        self.dropped_tools: frozenset[str] = frozenset()
        self.phase: str | None = None
        self.plan_list: str | None = None
        self.retry_stage: int | None = job.get('retry_stage') if job.get('retry_of') else None
        self.carried_stages: list[dict] = (job.get('stages') or []) if self.retry_stage is not None else []
        self.research: dict[str, list[str]] = {}  # #59: list id -> the plan's research column keys
        self.research_rows: set[str] = set()  # lists whose first rows of this turn went to research
        self.research_cap: dict[str, int] = {}  # list id -> the approved rows: filled cells per column, at most
        self.cancelled = asyncio.Event()  # set by _watch_cancel on the user's stop request

    # ── setup ──────────────────────────────────────────────────────────────

    def _load(self) -> list[dict]:
        with get_conn() as conn:
            session = conn.execute(
                'SELECT goal, project_id, brief FROM chat_sessions WHERE id = %s', (self.session_id,)
            ).fetchone()
            self.project_id = str(session['project_id']) if session and session['project_id'] else None
            profile = conn.execute(
                'SELECT profile FROM business_profiles WHERE project_id = %s', (self.project_id,)
            ).fetchone()
            self.profile_prompt = profile_prompt(profile['profile'] if profile else None)
            self.locale = turn_locale(conn, self.job_id, self.user_id)
            self.goal_prompt, self.dropped_tools = _goal(conn, self.session_id, session['goal'] if session else None)
            msgs = conn.execute(
                'SELECT role, content, question, sources, user_id FROM chat_messages'
                ' WHERE session_id = %s ORDER BY created_at',
                (self.session_id,),
            ).fetchall()
            for r in conn.execute(
                'SELECT DISTINCT ON (l.entity) l.id, l.entity FROM lists l'
                ' JOIN chat_sessions s ON s.id = l.chat_session_id AND s.organization_id = l.organization_id'
                ' WHERE s.id = %s AND l.deleted_at IS NULL ORDER BY l.entity, l.created_at DESC',
                (self.session_id,),
            ):
                self.lists[r['entity']] = str(r['id'])
                self.current_list = str(r['id'])
            # per cell, not per row: a save_to_list row merged into a vendor row can fill its domain (#407)
            for r in conn.execute(
                'SELECT c.value FROM list_rows r JOIN lists l ON l.id = r.list_id'
                ' JOIN chat_sessions s ON s.id = l.chat_session_id AND s.organization_id = l.organization_id'
                ' CROSS JOIN LATERAL jsonb_each_text(r.cells) c'
                ' WHERE s.id = %s AND l.deleted_at IS NULL AND c.key = ANY(%s)'
                " AND r.cell_meta->c.key->>'provider' IS DISTINCT FROM 'web'" + untrusted.NOT_TEAMMATE_CELL,
                (self.session_id, list(ROW_HOST_KEYS), self.user_id),
            ):
                if host := host_of(r['value']):
                    self.named.hosts.add(host)
        self.list_name = latest_ask(msgs)
        msgs = with_brief(msgs, session['brief'] if session else None)
        self.foreign_history, owner_hosts = untrusted.history(msgs, self.user_id)  # #963
        self.named.hosts |= owner_hosts
        for m in msgs:
            # only search results: a fetched page's url may be one an earlier turn read before #407
            for src in untrusted.own_sources(m, self.user_id):
                if src.get('kind') == SOURCE_KIND['web_search']:
                    self.named.add_page(src.get('url'))
        self.brief = '\n'.join(m['content'] for m in msgs if m['role'] == 'user')[-2000:]
        return model_history(msgs)

    def _system(self) -> str:
        system = SYSTEM_PROMPT + FAILED_SEARCH_RULE + '\n\n' + language_instruction(self.locale)
        if self.retry_stage is not None and self.carried_stages:
            system += '\n\n' + retry.prompt(self.carried_stages, self.retry_stage, self.budget - self.credits.spent)
        if self.profile_prompt:
            system += '\n\n' + self.profile_prompt
        if memory := prompt_section(memory_store.read(UUID(self.user_id)).body):
            system += '\n\n' + memory
        if self.goal_prompt:
            system += '\n\n' + self.goal_prompt
        return system

    def _state(self) -> str:
        """The chat's lists and plans. They change every turn, so they ride on the turn's user message, after the
        prefix both providers cache, never in the system prompt."""
        if not self.lists:
            return ''
        with get_conn() as conn:
            lines = list_lines(conn, self.lists)
            plan_lines = plans.prompt_section(
                conn, list(self.lists.values()), '' if self.retry_stage is not None else self.job_id
            )
        state = 'Lists already in this chat (new rows are appended to them):\n' + '\n'.join(lines)
        return state + '\n\n' + plan_lines if plan_lines else state

    def _run_plan(self, list_id: str, plan: dict, rows: int, credits: int) -> None:
        """This turn executes ``plan``: its list, its budget, its stages."""
        # #259: tools plus research fills (billed by their own task, capped at the approved rows) stay within
        # the plan's ceiling, never a floor above it; what the turn spent before proposing the plan stays its own
        keys = plans.research_keys(plan)
        self.budget, self.planned = self.credits.spent + plans.tool_budget(plan, rows, credits), True
        self.lists[plan['entity']] = self.current_list = self.plan_list = list_id
        if self.retry_stage is not None:
            with get_conn() as conn:
                self.budget = max(self.budget - retry.spent_before(conn, self.job_id), 0)
            self.stages = self.carried_stages
        else:
            self.stages = plans.stages_for(plan, rows)
        self.dropped_tools = plans.dropped_tools(plan)
        self._update_job(list_id=list_id, stages=Jsonb(self.stages), **stages.progress(self.stages))
        self._emit_stages()
        if keys and self.retry_stage is None:  # #259: research is no stage, so a retry never pays for it
            self.research[list_id] = keys
            self.research_cap[list_id] = rows
            self._fill_research(list_id)  # rows the list already has (enrich mode)

    def _fill_research(self, list_id: str) -> None:
        """#59: research the plan's AI columns on the rows the list has now; never fails the turn."""
        try:
            from app.worker import fill_plan_columns

            fill_plan_columns.delay(list_id, self.research[list_id], self.research_cap[list_id], self.job_id)
        except Exception:
            logger.exception('agent.research_enqueue_failed', job_id=self.job_id, list_id=list_id)

    def _stage_index(self, name: str) -> int | None:
        """The plan stage a call of ``name`` pays for, None when it pays for none. A retry turn pays for its
        retried stage only; otherwise the first unfinished stage of the tool, else its last."""
        if not self.stages:
            return None
        if self.retry_stage is not None:
            return self.retry_stage if self.stages[self.retry_stage]['tool'] == name else None
        mine = [i for i, s in enumerate(self.stages) if s['tool'] == name]
        return next((i for i in mine if self.stages[i]['done'] < self.stages[i]['units']), mine[-1] if mine else None)

    def _preflight(self, priced: PricedCall, planned_cost: int = 0) -> ToolResult | None:
        """The one gate every call passes before its handler runs, serial or parallel: a refusal, or
        None once the call's credits are covered (``planned_cost``: what earlier calls of the step hold)."""
        call, tool = priced.call, priced.tool
        if refusal := untrusted.refusal(self.tool_calls, call.name, self.foreign_history):  # #409: before the price
            return refusal
        if tool.provider and vendor_disabled_now(tool.provider):
            return ToolResult(ok=False, error='This data source is temporarily disabled.', error_code='disabled')
        if priced.behind in self.dropped_tools:  # more_rows counts as the search behind its list
            return ToolResult(ok=False, error='The user dropped this step from the plan. Do not run it.')
        # a free search is still a plan stage (rows are free since 2026-10-07): a retry turn reruns only its own
        stage = tool.price or call.name == 'more_rows' or any(s['tool'] == call.name for s in self.stages)
        if self.retry_stage is not None and stage and self._stage_index(priced.behind) is None:
            return ToolResult(
                ok=False, error='That plan step already ran: this turn retries only one step.', error_code='skipped'
            )
        if tool.price:
            need = planned_cost + priced.quote.credits
            return self._over_budget(need) or (None if self.credits.cover(need) else self.credits.refusal())
        if call.name in free_rows.GATED:
            hold, refusal = free_rows.check(self.session_id, self.credits.unspent, call.name)
            if hold:
                self.free_rows_holds[call.id] = hold
            return refusal
        return None

    def _begin_tool(self, call: ToolCall) -> None:
        self.emit('tool_start', {'tool_id': call.id, 'tool_name': call.name, 'tool_input': call.input})
        self._set_phase(call.name)
        self._emit_stages()

    def _set_phase(self, phase: str) -> None:
        self._update_job(phase=phase)
        self.phase = phase

    def _start_stage(self, priced: PricedCall) -> None:
        """Once a call passed its preflight. The failed step's error clears only once its retry really starts,
        so a retry that never gets that far leaves it retryable."""
        if (i := self._stage_index(priced.behind)) is None:
            return
        self.stage_lists[i] = self.current_list  # the list _context hands the tool
        retried = i == self.retry_stage and self.stages[i].pop('error', None)
        if stages.start(self.stages[i], bool(retried)):
            self._update_job(stages=Jsonb(self.stages))
            self._emit_stages()

    def _over_budget(self, cost: int) -> ToolResult | None:
        if self.credits.spent + cost <= self.budget:
            return None
        if self.planned:
            error = (
                f'The approved plan allows at most {self.budget} credits and this call would exceed it. '
                'Stop and tell the user what is left to do.'
            )
        else:
            error = (
                f'This would take the run over {self.budget} credits. Call propose_plan for the whole run and stop: '
                'the user approves large runs on the plan card.'
            )
        return ToolResult(ok=False, error=error, error_code='over_budget')

    # ── loop ───────────────────────────────────────────────────────────────

    async def _call_model(self, system: str, messages: list[dict], tools: list[dict]) -> tuple[str, list[ToolCall]]:
        step = StepText(lambda text: self.emit('text', {'content': text}))
        stream = self.provider.chat_stream(system=system, messages=messages, tools=tools)
        # a stop request cuts the model off mid-answer; the text it streamed so far is kept
        model, stop = asyncio.ensure_future(step.read(stream)), asyncio.ensure_future(self.cancelled.wait())
        cut = True
        try:
            await asyncio.wait({model, stop}, return_when=asyncio.FIRST_COMPLETED)
            cut = not model.done()
        finally:  # the turn deadline cuts it off the same way
            stop.cancel()
            if cut:
                model.cancel()
                await asyncio.gather(model, return_exceptions=True)
                self.answer.keep(step.end())
        if cut:
            raise JobCancelled
        model.result()
        return step.end(), step.calls

    def _check_cancelled(self) -> None:
        """The stop point between steps and tools, and after every tool batch before anything else spends.
        It asks Redis too: a stop that landed while a tool held the loop may not have reached the watcher yet.

        ponytail: a tool that is running finishes first (it may hold vendor calls and credits); interrupt
        handlers too if a single long tool makes «Ferma» feel slow."""
        if self.cancelled.is_set() or events.cancel_requested(self.job_id):
            self.cancelled.set()
            raise JobCancelled

    async def _watch_cancel(self) -> None:
        """Set ``cancelled`` when the user's stop request lands: a blocking stream read, no polling."""
        try:
            async for item in events.read_stream(events.cancel_key(self.job_id), '0-0'):
                if item is not None:
                    self.cancelled.set()
                    return
        except Exception as exc:  # Redis down: the job runs to its end, as before the stop button
            logger.warning('agent.cancel_watch_failed', job_id=self.job_id, error_type=type(exc).__name__)

    async def _loop(self, messages: list[dict]) -> None:
        system = self._system()
        if (
            (state := self._state())
            and messages
            and messages[-1]['role'] == 'user'
            and isinstance(messages[-1]['content'], str)
        ):
            messages[-1]['content'] += f'\n\n<chat_state>\n{state}\n</chat_state>'
        if self.pick:
            tool, args = (
                (self.pick['tool'], self.pick['args']) if 'args' in self.pick else ('search_companies', self.pick)
            )
            call = ToolCall(f'pick_{self.job_id.replace("-", "")}', tool, args)
            messages.append(
                {
                    'role': 'assistant',
                    'content': [{'type': 'tool_use', 'id': call.id, 'name': call.name, 'input': call.input}],
                }
            )
            results = await self._run_tools([call])
            self._check_cancelled()  # before the funnel's paid pages
            if await self._no_results():
                return
            messages.append(
                {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': call.id, 'content': results[0]}]}
            )
        for step in range(MAX_STEPS):
            self._check_cancelled()
            if step:
                self.emit('agent_thinking', {'turn_seq': step})
            tools = [
                t for t in registry.spec_list() if t['name'] not in self.dropped_tools
            ]  # a plan approved mid-turn changes it
            text, calls = await self._call_model(system, messages, tools)
            self.answer.add(text, calls)
            if not calls:
                return
            messages.append(
                {
                    'role': 'assistant',
                    'content': ([{'type': 'text', 'text': text}] if text else [])
                    + [{'type': 'tool_use', 'id': c.id, 'name': c.name, 'input': c.input} for c in calls],
                }
            )
            results = await self._run_tools(calls)
            self._check_cancelled()  # before a plan's research fills or the funnel's paid pages
            if self._paused(calls, results) or await self._no_results():
                return
            self._set_phase('scoring')
            await self._score()
            messages.append(
                {
                    'role': 'user',
                    'content': [
                        {'type': 'tool_result', 'tool_use_id': call.id, 'content': result}
                        for call, result in zip(calls, results, strict=True)
                    ],
                }
            )
        self.terminal_code = 'max_turns_reached'
        self.answer.give_back()
        self.emit('error', {'message': 'Stopped after the maximum number of steps.', 'code': self.terminal_code})

    def _paused(self, calls: list[ToolCall], results: list[str]) -> bool:
        for call, result in zip(calls, results, strict=True):
            data = json.loads(result) if call.name in STEP_ENDING_TOOLS else {}
            if call.name == 'propose_plan' and 'list_id' in data:
                self.lists[data['entity']] = self.current_list = data['list_id']
                if data['needs_approval']:
                    # the plan card is the question; the approval is the next chat message
                    self.question = {'question': '', 'options': [], 'plan': data}
                    return True
                cap = self.budget
                self._run_plan(data['list_id'], data, data['target_rows'], data['estimated_credits'])
                self.budget = min(cap, self.budget)  # #259: a plan nobody approved never raises the turn's cap
            elif data.get('question'):
                self.question = data
                return True
        return False

    async def _no_results(self) -> bool:
        """An Apollo search that found nothing ends the turn on the funnel of its own filters, once per turn;
        one whose Google Maps fallback is still to run waits for it to come back empty too."""
        if self.found_rows or not self.empty_search or self.local_pending or self.funnel_tried:
            return False
        current_user_id.set(self.user_id)
        client = ApolloClient()
        if not client.is_configured or await asyncio.to_thread(vendor_disabled_now, 'apollo'):
            return False
        entity, filters = self.empty_search

        async def count(filters: dict) -> int | None:
            if entity == 'person':
                people = await discover_people(client, filters, 1, per_page=1)
                return None if is_err(people) else people.value.total_entries
            found = await discover_companies(client, filters, 1)
            return None if is_err(found) else found.value.total_entries

        # #259: built once per turn, even when it fails (an Apollo count failed): a rebuild spends pages again
        self.funnel_tried = True
        self.question = await funnel.build(count, filters, entity)
        return self.question is not None

    async def _run_tools(self, calls: list[ToolCall]) -> list[str]:
        """Run only distinct, explicitly read-only searches concurrently."""
        if ending := next((call for call in calls if call.name in STEP_ENDING_TOOLS), None):
            # one question or plan per step: the rest could change what it does, or save a plan no card shows (#465)
            skipped = json.dumps({'error': f'skipped: {"/".join(sorted(STEP_ENDING_TOOLS))} must be called alone'})
            return [await self._run_tool(call) if call is ending else skipped for call in calls]
        if not _parallelizable(calls):
            results = []
            for call in calls:
                self._check_cancelled()
                results.append(await self._run_tool(call))
            return results
        return await self._run_parallel_searches(calls)

    async def _run_parallel_searches(self, calls: list[ToolCall]) -> list[str]:
        self._check_cancelled()
        # Prepare every call, including its credit hold, serially before a provider
        # handler can start. This keeps Turn's reservation counters race-free.
        tools = {name: tool for name in PARALLEL_TOOL_ALLOWLIST if (tool := registry.get(name)) is not None}
        if any(call.name not in tools for call in calls):
            return [await self._run_tool(call) for call in calls]
        prepared: list[tuple[PricedCall, ToolResult | None]] = []
        planned_cost = 0
        for call in calls:
            await self._in_thread(self._begin_tool, call)
            priced = await self._in_thread(PricedCall.of, call, tools[call.name], self.session_id)
            result = await self._in_thread(self._preflight, priced, planned_cost)
            if result is None:
                planned_cost += priced.quote.credits
                await self._in_thread(self._start_stage, priced)
            prepared.append((priced, result))

        semaphore = asyncio.Semaphore(MAX_PARALLEL_TOOLS)

        async def execute(priced: PricedCall) -> tuple[ToolResult, list[str]]:
            async with semaphore:
                return await self._handle(priced)

        active = [priced for priced, result in prepared if result is None]
        try:  # _handle raises no Exception; ActorGone or the deadline cancels the other searches, before a vendor call
            async with asyncio.TaskGroup() as group:
                running = {priced.call.id: group.create_task(execute(priced)) for priced in active}
        except BaseExceptionGroup as failed:
            raise failed.exceptions[0] from None

        results = []
        for priced, preflight_error in prepared:
            result, called = (preflight_error, []) if preflight_error else running[priced.call.id].result()
            results.append(await self._in_thread(self._finish_tool, priced.call, priced.tool, result, called, priced))
        return results

    async def _in_thread[T](self, fn: Callable[..., T], *args: object) -> T:
        """``fn`` changes the turn's spend, holds or rows: a cancel (the turn deadline) waits for it to finish,
        or the settle that follows would miss what it bills."""
        # A plain future, not a task: asyncio.run's shutdown cancels every task, and would stop the wait below.
        work = asyncio.get_running_loop().run_in_executor(None, contextvars.copy_context().run, fn, *args)
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            await work
            raise

    async def _handle(self, priced: PricedCall) -> tuple[ToolResult, list[str]]:
        """Run the handler; never raises. Also returns the vendors it called, for the transcript (#352)."""
        with track_vendors() as called:
            try:
                return await priced.tool.handler(priced.call.input or {}, self._context(priced)), called
            except Exception:
                logger.exception('agent.tool_crashed', tool=priced.call.name, job_id=self.job_id)
                return ToolResult.crashed(), called

    async def _run_tool(self, call: ToolCall) -> str:
        tool = registry.get(call.name)
        self._begin_tool(call)
        called: list[str] = []
        priced = PricedCall.of(call, tool, self.session_id) if tool and not call.error else None
        if priced is None:
            result = ToolResult(ok=False, error=call.error or f'Unknown tool {call.name}.')
        elif refusal := self._preflight(priced):
            result = refusal
        else:
            self._start_stage(priced)
            result, called = await self._handle(priced)
        return self._finish_tool(call, tool, result, called, priced)

    def _context(self, priced: PricedCall) -> ToolContext:
        return ToolContext(
            user_id=self.user_id,
            job_id=self.job_id,
            list_id=self.current_list,
            session_id=self.session_id,
            project_id=self.project_id,
            named=self.named,
            max_units=priced.quote.rows if priced.tool.price else None,
            claims=Claimer(self.job_id, priced.call.id, self.current_list, self.emit),
        )

    def _finish_tool(
        self,
        call: ToolCall,
        tool: registry.ToolDef | None,
        result: ToolResult,
        called: list[str],
        priced: PricedCall | None,
    ) -> str:
        if result.ok and result.rows:
            self._drop_excluded(result)
        try:
            list_info, cost = self._deliver(priced, result) if priced else ({}, 0)
        except HoldGone:  # nothing of the call is stored or billed: it fails like a crash, and the turn goes on
            logger.exception('agent.hold_gone', tool=call.name, job_id=self.job_id)
            release_call(self.job_id, call.id)  # the rollback kept the claim
            result, list_info, cost = ToolResult.crashed(), {}, 0
        if result.ok:
            self.named.add_result(
                call.name, result.data, [*result.rows, *(u.get('cells') or {} for u in result.row_updates)]
            )
        if hold := self.free_rows_holds.pop(call.id, None):
            free_rows.settle(hold, len(result.rows) if result.ok else 0)
        self.found_rows = self.found_rows or bool(result.ok and result.rows)
        if result.ok and not self.empty_search and (applied := result.data.get('applied_filters')):
            self.empty_search = (result.entity or 'company', applied)
            self.local_pending = 'local_fallback' in result.data
        elif result.ok and result.data.get('source') == 'google_maps' and not result.rows:
            self.local_pending = False  # Maps found nothing either: the funnel explains the B2B search
        if list_info and result.rows:
            remember_result(list_info['list_id'], call.name, call.input or {}, result)
        payload = (
            {**result.data, **({'list': list_info} if list_info else {})} if result.ok else {'error': result.error}
        )
        preview = json.dumps(payload, default=str)[:PREVIEW_CHARS]
        verdict = 'success' if result.ok else 'failed'
        summary = summary_of(tool, result, list_info)
        typed = {
            **({'summary': summary.model_dump(mode='json')} if summary else {}),
            **({'brief': result.brief} if result.brief else {}),
            **({'error_code': result.error_code} if result.error_code else {}),
            **({'memory_saved': result.memory_saved} if result.memory_saved else {}),
            **({'campaign': result.campaign} if result.campaign else {}),
        }
        self.emit(
            'tool_result',
            {
                'tool_id': call.id,
                'tool_name': call.name,
                'result_preview': preview,
                'status': verdict,
                **typed,
            },
        )
        if cost:
            self._emit_credits(call.id, call.name, cost)
        first = list_info.get('rows_added') and list_info['list_id'] not in self.research_rows
        if first and list_info['list_id'] in self.research:  # the first rows, so results show early
            self.research_rows.add(list_info['list_id'])
            self._fill_research(list_info['list_id'])
        if list_info:
            self.emit(
                'list_updated',
                {k: list_info[k] for k in ('list_id', 'entity', 'rows_added', 'rows_updated', 'row_count')},
            )
        self.tool_calls.append(
            {
                'id': call.id,
                'name': call.name,
                'input': call.input,
                'result': preview,
                'credits_used': cost,
                'verdict': verdict,
                **({'vendors': called} if called else {}),
                **typed,
            }
        )
        if result.ok and call.name in SOURCE_KIND:
            self._harvest_sources(call, result.data)
        if self.stages:
            self._advance_stage(call, result, priced)
        else:
            self._update_job(progress_done=len(self.tool_calls), credits_spent=self.credits.spent)
        return json.dumps(payload, default=str)[:MODEL_RESULT_CHARS]

    def _drop_excluded(self, result: ToolResult) -> None:
        """The project's exclusions never reach a List, so nothing downstream enriches or contacts them."""
        with get_conn() as conn:
            rules = load_rules(conn, self.project_id)
        kept = [r for r in result.rows if not is_excluded(rules, r)]
        if skipped := len(result.rows) - len(kept):
            result.rows = kept
            result.data = {**result.data, 'excluded_by_user': skipped}

    def _advance_stage(self, call: ToolCall, result: ToolResult, priced: PricedCall | None) -> None:
        row_stages.fit(self.job_id, self.stages, self.stage_lists)
        if (i := self._stage_index(priced.behind if priced else call.name)) is not None:
            stage, tool = self.stages[i], priced and priced.tool
            stages.finish(stage)
            if result.ok:
                searched = searched_of(result)
                stage['done'] += searched if searched is not None else (priced.quote.rows if priced else 1)
                stage.pop('error', None)
            elif stage['done'] < stage['units'] and result.error_code != 'skipped':
                stage['error'] = {'code': result.error_code or ('provider' if tool and tool.provider else 'failed')}
        self._update_job(**stages.progress(self.stages), stages=Jsonb(self.stages), credits_spent=self.credits.spent)
        self._emit_stages()

    def _fail_open_stage(self) -> None:
        """A crash marks the stage that was running: the retried one in a retry turn."""
        open_stages = [i for i, s in enumerate(self.stages) if s['done'] < s['units']]
        if self.retry_stage is not None:
            i = self.retry_stage if self.retry_stage in open_stages else None
        else:
            i = next((i for i in open_stages if self.stages[i]['tool'] == self.phase), None)
            i = i if i is not None else next((i for i in open_stages if not self.stages[i].get('error')), None)
        if i is not None and not self.stages[i].get('error'):
            self.stages[i]['error'] = {'code': 'failed'}
            self._update_job(stages=Jsonb(self.stages))

    def _emit_stages(self) -> None:
        if self.stages:
            self.emit(
                'stages',
                {
                    'stages': copy.deepcopy(self.stages),
                    'phase': self.phase,
                    'credits_spent': self.credits.spent,
                    'retry_stage': self.retry_stage,
                    'list_id': self.plan_list,
                },
            )

    def _emit_credits(self, tool_id: str, tool_name: str, cost: int, **refund: object) -> None:
        left = self.credits.remaining
        self.emit('credits', {'tool_id': tool_id, 'tool_name': tool_name, 'cost': cost, 'remaining': left, **refund})

    def _finish_credits(self) -> None:
        given, charged_lists = self.credits.finish()
        calls = [c for c in self.tool_calls if c['id'] in given]
        for call in calls:
            call['credits_used'] -= given[call['id']][0]
            if call.get('summary'):  # a tool with no found_noun has none
                call['summary'] = with_refund(call['summary'], given[call['id']][1])
        for call in calls:  # after every count: a frame that fails to send loses no refund
            summary = {'summary': call['summary']} if call.get('summary') else {}
            self._emit_credits(call['id'], call['name'], -given[call['id']][0], refund=True, **summary)
        entities = {lid: e for e, lid in self.lists.items()}
        for list_id, n in Counter(charged_lists).items():
            self.emit('list_updated', {'list_id': list_id, 'entity': entities.get(list_id), 'rows_updated': n})

    def _deliver(self, priced: PricedCall, result: ToolResult) -> tuple[dict, int]:
        if not result.ok or not (result.rows or result.row_updates):
            release_call(self.job_id, priced.call.id)  # no answers to replace the claim
            return {}, 0
        entity = result.entity or 'company'
        with get_conn() as conn:
            self.credits.lock(conn, priced.quote.credits)
            if result.rows and entity not in self.lists:
                self.lists[entity] = create_list(
                    conn,
                    user_id=self.user_id,
                    session_id=self.session_id,
                    name=self.list_name,
                    entity=entity,
                )
            list_id = self.lists.get(entity) if result.rows else self.current_list
            release(conn, self.job_id, priced.call.id)  # with the answers below: no reader sees a gap
            if not list_id:
                return {}, 0
            conn.execute('UPDATE jobs SET list_id = coalesce(list_id, %s) WHERE id = %s', (list_id, self.job_id))
            entity = conn.execute('SELECT entity FROM lists WHERE id = %s', (list_id,)).fetchone()['entity']
            new_ids = add_rows(conn, list_id, entity, result.rows) if result.rows else []
            if new_ids:
                self.grown[list_id] = entity
                conn.execute(
                    'UPDATE jobs SET first_row_at = now() WHERE id = %s AND first_row_at IS NULL', (self.job_id,)
                )
            delivered = deliver(conn, self.credits, priced.quote, priced.call.id, result.row_updates, list_id, entity)
            count = conn.execute('SELECT count(*) AS n FROM list_rows WHERE list_id = %s', (list_id,)).fetchone()['n']
        self.credits.spent += delivered.credits
        self.current_list = list_id
        return {
            'list_id': list_id,
            'entity': entity,
            'rows_added': len(new_ids),
            **delivered.counts,
            'row_count': count,
            'row_ids': new_ids,
        }, delivered.credits

    def _harvest_sources(self, call: ToolCall, data: dict) -> None:
        results = data.get('results')
        items = results if isinstance(results, list) else [data]
        for it in items:
            url = isinstance(it, dict) and it.get('url')
            if url and url not in self.sources:
                self.sources[url] = {
                    'url': url,
                    'title': it.get('title') or '',
                    'domain': urlparse(url).netloc,
                    'kind': SOURCE_KIND[call.name],
                    'snippet': it.get('snippet') or '',
                    'tool_id': call.id,
                }

    def _update_job(self, **cols: object) -> None:
        sets = ', '.join(f'{k} = %s' for k in cols)
        with get_conn() as conn:
            conn.execute(f'UPDATE jobs SET {sets}, updated_at = now() WHERE id = %s', (*cols.values(), self.job_id))

    # ── relevance score ────────────────────────────────────────────────────

    async def _score(self) -> None:
        schema = {
            'type': 'object',
            'properties': {
                'scores': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'id': {'type': 'string'},
                            'score': {'type': 'number'},
                            'reasons': {
                                'type': 'array',
                                'items': {
                                    'type': 'object',
                                    'properties': {
                                        'match': {'type': 'string'},
                                        'category': {'type': 'string', 'enum': list(REASON_CATEGORIES)},
                                        'freshness': {'type': ['string', 'null']},
                                        'key': {'type': ['string', 'null']},
                                    },
                                    'required': ['match', 'category', 'freshness', 'key'],
                                },
                            },
                        },
                        'required': ['id', 'score', 'reasons'],
                    },
                }
            },
            'required': ['scores'],
        }
        for entity, list_id in self.lists.items():
            last_seq = self.score_last_seq.get(entity, 0)
            scored = 0
            while self.score_batches.get(entity, 0) < SCORE_MAX_BATCHES:
                self._check_cancelled()
                with get_conn() as conn:
                    rows = conn.execute(
                        'SELECT id, seq, cells FROM list_rows WHERE list_id = %s AND relevance_score IS NULL'
                        ' AND NOT excluded AND seq > %s ORDER BY seq LIMIT %s',
                        (list_id, last_seq, SCORE_BATCH),
                    ).fetchall()
                if not rows:
                    break
                last_seq = rows[-1]['seq']
                self.score_last_seq[entity] = last_seq
                self.score_batches[entity] = self.score_batches.get(entity, 0) + 1
                prompt = (
                    'Brief:\n' + self.brief + f'\n\nScore each {entity} 0-100 for fit with the brief. '
                    f'For each, give up to {MAX_REASONS} reasons: the strongest matches (or mismatches) to the '
                    'brief, each one short sentence citing only the data shown. category is the kind of '
                    'evidence. key is the field the evidence comes from, or null. freshness is how recent the '
                    'evidence is, only when a date appears in the data, else null. '
                    'Return {"scores": [{"id": ..., "score": ..., "reasons": [...]}]}.\n\n'
                    + json.dumps([{'id': str(r['id']), **_score_cells(r['cells'])} for r in rows], default=str)[:30_000]
                )
                try:
                    resp = await self.provider.chat(
                        system='You score B2B leads. Reply with JSON only.',
                        messages=[{'role': 'user', 'content': prompt}],
                        max_tokens=8000,
                        response_format=json_schema('scores', schema),
                    )
                    scores = json.loads(resp.text)['scores']
                except Exception as e:
                    logger.warning('agent.score_failed', job_id=self.job_id, error=str(e)[:200])
                    break
                by_id = {str(r['id']): r['cells'] for r in rows}
                with get_conn() as conn:
                    for s in scores:
                        cells = by_id.get(str(s.get('id')))
                        if cells is not None and isinstance(s.get('score'), int | float):
                            conn.execute(
                                'UPDATE list_rows SET relevance_score = %s, score_reasons = %s WHERE id = %s',
                                (
                                    max(0, min(100, s['score'])),
                                    _jsonb_or_null(clean_reasons(s.get('reasons'), cells)),
                                    s['id'],
                                ),
                            )
                            scored += 1
            if scored:
                self.emit('list_updated', {'list_id': list_id, 'entity': entity, 'rows_scored': scored})

    # ── run ────────────────────────────────────────────────────────────────

    async def run(self) -> None:
        set_metering_context(MeteringContext(user_id=self.user_id, session_id=self.session_id, endpoint='agent'))
        status, error, interrupted = 'succeeded', None, None
        if await asyncio.to_thread(events.cancel_requested, self.job_id):
            self.cancelled.set()  # stopped while queued: end before the first model call
        watcher = asyncio.create_task(self._watch_cancel())
        try:
            messages = self._load()
            with get_conn() as conn:
                approved = plans.for_job(conn, self.job_id)
            if approved:
                self._run_plan(
                    approved['list_id'], approved['plan'], approved['approved_rows'], approved['approved_credits']
                )
            self.credits.reserve()
            async with asyncio.timeout(TURN_DEADLINE_SECONDS):
                await self._loop(messages)
                self._check_cancelled()  # before the last fit score and the research fills
                self._set_phase('scoring')
                await self._score()
            for list_id in self.research:
                self._fill_research(list_id)  # the rest, best fit first
        except JobCancelled:
            status, self.terminal_code, self.question = 'cancelled', 'cancelled', None
            self.answer.give_back()
            self.emit('error', {'message': 'Stopped at your request.', 'code': self.terminal_code})
        except ActorGone:  # the member left or was banned: no more model or tool calls, and the organization ends it
            status, error = 'failed', turn_scope.GONE_ERROR
            self.answer.give_back()
            self.emit('error', turn_scope.GONE)
        except BaseException as e:
            # Celery's soft time limit reaches the turn as a CancelledError from asyncio.run: the turn still settles
            # and saves what it did, then re-raises it.
            logger.exception('agent.job_failed', job_id=self.job_id)
            status, error = 'failed', str(e)[:500] or type(e).__name__
            interrupted = None if isinstance(e, Exception) else e
            self.answer.give_back()
            self.emit('error', {'message': 'Something went wrong running this request.', 'code': 'CHAT.TURN_FAILED'})
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
        with turn_scope.ending(self.org_id):
            self._end(status, error, interrupted)

    def _end(self, status: str, error: str | None, interrupted: BaseException | None) -> None:
        if error:
            self._fail_open_stage()
        for hold in self.free_rows_holds.values():  # searches the deadline cut off returned no rows
            free_rows.settle(hold, 0)
        try:
            self._finish_credits()
        except Exception:
            # A failure before TurnCredits.finish commits leaves the hold reserved, and the reaper settles it from its
            # charges after RESERVATION_STALE_AFTER_MINUTES; a failure after that commit loses only frames.
            logger.exception('agent.settle_failed', job_id=self.job_id, tab=self.credits.tab)
        self.phase = None
        self._emit_stages()
        if error:
            self.question = None  # a failed turn is not waiting on an answer
        elif self.question:
            status = 'needs_input'
        timing = None
        msg_status = 'failed' if error else 'partial' if self.terminal_code else 'success'
        # refunded/undelivered: the back office's note on the job (#352)
        job_result = {'lists': self.lists, 'refunded': self.credits.refunded, 'undelivered': self.credits.undelivered}
        try:
            with get_conn() as conn:
                msg_id = conn.execute(
                    'INSERT INTO chat_messages (session_id, user_id, role, content, tool_calls, sources, credits_used,'
                    ' status, terminal_code, job_id, question) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id',
                    (
                        self.session_id,
                        self.user_id,
                        'assistant',
                        '\n\n'.join(self.answer.parts),
                        Jsonb(self.tool_calls),
                        Jsonb(list(self.sources.values())),
                        self.credits.spent,
                        msg_status,
                        self.terminal_code,
                        self.job_id,
                        Jsonb(self.question) if self.question else None,
                    ),
                ).fetchone()['id']
                conn.execute('UPDATE chat_sessions SET updated_at = now() WHERE id = %s', (self.session_id,))
                timing = conn.execute(
                    'UPDATE jobs SET status = %s, phase = NULL, error = %s, credits_spent = %s, result = %s,'
                    ' finished_at = now(), updated_at = now() WHERE id = %s RETURNING started_at, first_row_at',
                    (status, error, self.credits.spent, Jsonb({'message_id': str(msg_id), **job_result}), self.job_id),
                ).fetchone()
            saved = {'message_id': str(msg_id), 'status': msg_status}
            self.emit('message_saved', {**saved, 'question': self.question} if self.question else saved)
        except Exception:
            # job stays 'running'; the next send marks it failed (routes._fail_stale_jobs)
            logger.exception('agent.persist_failed', job_id=self.job_id)
            self.emit('error', {'message': 'The answer could not be saved.', 'code': 'CHAT.SAVE_FAILED'})
        self.emit('done', {'sources': list(self.sources.values()), 'list_id': self.current_list, 'job_id': self.job_id})
        if timing and error != turn_scope.GONE_ERROR:  # E4, #215: rows found, a stopped stage or a question waiting
            try:
                outcome = notifications.turn_outcome(
                    status,
                    self.stages,
                    self.question,
                    bool(timing['first_row_at']),
                    self.credits.refunded,
                    self.credits.spent,
                    self.credits.undelivered,
                )
                with get_conn() as conn:
                    notified = notifications.notify_turn(
                        conn, self.user_id, self.session_id, timing['started_at'], outcome
                    )
                notifications.deliver(notified)
            except Exception as exc:
                logger.error('agent.notify_failed', job_id=self.job_id, error_type=type(exc).__name__)
        if timing and status == 'succeeded':
            for list_id, entity in self.grown.items():
                webhooks.emit(
                    'list.completed',
                    {'list_id': list_id, 'entity': entity, 'chat_session_id': self.session_id, 'job_id': self.job_id},
                )
        if interrupted:
            raise interrupted


async def run_job(job_id: str, *, provider: LLMProvider | None = None, emit: Emit | None = None) -> None:
    """Entry point of the Celery task. No-op unless the job is still queued, so
    a redelivered task never runs a turn twice."""
    job = turn_scope.claim(job_id)
    if not job:
        logger.warning('agent.job_not_queued', job_id=job_id)
        return
    if provider is None:
        from app.llm.router import get_chat_provider

        provider = get_chat_provider()
    if emit is None:
        from app.agent.events import Publisher

        emit = Publisher(job_id)
    await Turn(job, provider, emit).run()
