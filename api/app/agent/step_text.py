"""The text the model writes: per step, held back while it may still announce the step's tool calls (#572), and
per turn, the answer those steps leave (#567).

SYSTEM_PROMPT forbids text before a tool call. DeepSeek writes one short sentence there anyway, in English
whatever the user's language ("I'll plan this list before spending anything."), and no prompt wording stopped it.
So a step that calls a tool drops the text it still holds; a longer text streams as before."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from app.llm.base import LLMEvent, ToolCall

# When every search of a turn fails, the web shows its own line with the retry (chat.steps.allFailed). Measured with
# DeepSeek on an offline replay of such a turn, 4-6 runs per wording: any wording that still let the answer mention
# the failure repeated that line in every run, and an exception for turns where the line does not show was applied
# to all turns or to none. So the answer never mentions it; there, the failed step still shows its error.
FAILED_SEARCH_RULE = """
- When every search of a turn fails because a source did not answer, the chat itself shows the user a fixed line \
right above your answer: the search did not succeed, whether credits were used, retry shortly. Your answer comes \
under that line, so it must not say any of that again, and this overrides the rule about explaining a short List: \
no sentence about the failure, the sources, the empty List, credits or retrying. Your answer is only what the line \
does not say, such as another way to reach the same people (never connecting an account) or a change to the brief, \
in one or two sentences, and its first sentence is that suggestion (for example: "If you name a few of the hotels \
you have in mind, I can look for their marketing managers."). Otherwise, after a partial result, say what was found and keep \
the rest of the answer true to it, and say that no credits were used only when no tool result of this turn charged \
any. When a tool error says the balance is too low, tell the user to top up credits instead of retrying. Never \
name a data source, vendor, API key or configuration ("not configured", "unavailable"), never suggest connecting \
an account (LinkedIn included) as a way around a failure unless the user asked for that account's search, and \
never blame a missing integration."""

# Every announcement seen was one sentence under 80 characters; an answer longer than this streams from here.
HOLD_CHARS = 200
# Tools that end a step: what they return may change the rest of it.
STEP_ENDING_TOOLS = frozenset({'ask_user', 'propose_plan'})


class StepText:
    def __init__(self, emit: Callable[[str], None]) -> None:
        self._emit = emit
        self._held = ''
        self.shown = ''
        self.calls: list[ToolCall] = []

    async def read(self, events: AsyncIterator[LLMEvent]) -> None:
        async for ev in events:
            if ev.type == 'text_delta' and ev.text:
                self._held += ev.text
                if self.shown or len(self._held) > HOLD_CHARS:
                    self._flush()
            elif ev.type == 'tool_end' and ev.tool_call:
                self.calls.append(ev.tool_call)

    def end(self) -> str:
        if not self.calls:
            self._flush()
        return self.shown

    def _flush(self) -> None:
        if self._held:
            self._emit(self._held)
            self.shown += self._held
            self._held = ''


class Answer:
    """The answer reads only what a step showed, so a short announcement dropped by StepText never reaches it. Text
    that streamed past the hold in a step that also calls a tool is taken off the chat and held, except beside a
    question or a plan card it leads into. The held text comes back when the model writes nothing else before the turn
    ends or before its question or plan card: the model answered with its last call, or the turn was cut short before
    it could write again."""

    def __init__(self, emit: Callable[[str, dict], None]) -> None:
        self.emit = emit
        self.parts: list[str] = []
        self.held = ''

    def add(self, text: str, calls: list[ToolCall]) -> None:
        ending = any(c.name in STEP_ENDING_TOOLS for c in calls)
        if not text:
            if not calls or ending:
                self.give_back()
        elif calls and not ending:
            self.held = text
            self.emit('text_dropped', {})
        else:
            self.keep(text)

    def keep(self, text: str) -> None:
        if text:
            self.parts.append(text)
            self.held = ''

    def give_back(self) -> None:
        if self.held:
            self.emit('text', {'content': self.held})
            self.keep(self.held)
