"""A ``chat_messages.tool_calls`` entry as both message routes send it, read from every shape the loop ever stored.

The loop is the only writer. The record grew in steps: M1 (#4) wrote ``id, name, input, result, credits_used,
verdict``; #246 and the campaign draft added ``summary, brief, memory_saved, campaign``; #424 added ``vendors``;
#530 and #666 changed ``summary``. Old rows are read here and never migrated: a rewrite would break a rollback,
because the previous image reads ``summary.count``, and an old worker can write the old shape during a deploy.
Nothing here raises: one bad entry must never fail a session load.
"""

from typing import Annotated, Any, Literal

import structlog
from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError, field_validator, model_validator

from app.agent.summary import SUMMARY, ToolSummary
from app.lists.lookup_outcome import Searched, Tally, kind_of

logger = structlog.get_logger()


def _current(raw: Any) -> Any:
    """The current shape of a stored summary; a shape this cannot read comes back unchanged and fails validation."""
    if not isinstance(raw, dict) or 'kind' in raw or not isinstance(raw.get('count'), int):
        return raw
    noun = raw.get('noun')
    if noun in ('company', 'person'):
        return {'kind': 'count', 'noun': noun, 'count': raw['count']}
    if noun in ('email', 'phone'):
        searched, failed = raw.get('searched'), raw.get('failed')
        found = raw['count']
        stored_refund = raw.get('refunded')
        refunded = stored_refund if isinstance(stored_refund, int) else 0
        # Before #713 `failed` left out people with no source, a phone `searched` counted rows that could not be
        # searched, and `invalid` was not split out: the remainder is not «not found». Only a tally the stored counts
        # determine is a Searched; any other keeps `found`, plus `failed`, which a retry can act on (#548).
        if isinstance(searched, int) and isinstance(failed, int):
            all_found = found == searched and failed == 0
            all_failed = found == 0 and failed == searched > 0
            if all_found or all_failed:
                tally = Tally(found=found, failed=failed)
                return Searched(
                    kind=kind_of(tally),
                    contact=noun,
                    searched=searched,
                    tally=tally,
                    refunded=refunded,
                ).model_dump()
        return {
            'kind': 'legacy',
            'contact': noun,
            'found': found,
            'failed': failed if isinstance(failed, int) else 0,
            'refunded': refunded,
        }
    return raw


def _read_summary(raw: Any) -> Any:
    """The summary as a model, or None for a shape that cannot be read (logged). A model comes back unchanged."""
    if raw is None or isinstance(raw, BaseModel):
        return raw
    try:
        return SUMMARY.validate_python(_current(raw))
    except ValidationError:
        logger.warning('chat.tool_summary_unreadable', summary_type=type(raw).__name__)
        return None


class ToolCallRecord(BaseModel):
    model_config = ConfigDict(extra='ignore')  # old rows hold keys that a new model does not name
    id: str | None = None
    name: str
    input: dict = {}
    result: str | None = None
    credits_used: int = 0
    verdict: Literal['success', 'failed'] | None = None
    summary: ToolSummary | None = None
    brief: dict | None = None
    memory_saved: str | None = None
    campaign: dict | None = None
    error_code: str | None = None

    @field_validator('summary', mode='wrap')
    @classmethod
    def _stored(cls, raw: Any, handler: Any) -> Any:
        return handler(_read_summary(raw))

    @model_validator(mode='wrap')
    @classmethod
    def _guard(cls, data: Any, handler: Any) -> Any:
        """A field that fails to parse becomes its default, logged once. Without a ``name`` the entry is no call."""
        if isinstance(data, dict) and 'summary' in data:
            data = {**data, 'summary': _read_summary(data['summary'])}  # read once: the retry below must not log again
        try:
            return handler(data)
        except ValidationError as exc:
            bad = {str(e['loc'][0]) for e in exc.errors() if e['loc']}
            if 'name' in bad or not isinstance(data, dict):
                raise
            logger.warning('chat.tool_call_field_unreadable', fields=sorted(bad), tool=data.get('name'))
            # The default is put in, not left out: a field that is unset is not sent, and the web reads it as sent.
            return handler({**data, **{f: cls.model_fields[f].get_default(call_default_factory=True) for f in bad}})


class AdminToolCallRecord(ToolCallRecord):
    vendors: list[str] | None = None


def _readable(model: type[ToolCallRecord]) -> BeforeValidator:
    def read(raw: Any) -> Any:
        if not isinstance(raw, list):
            return None
        calls = []
        for entry in raw:
            try:
                calls.append(model.model_validate(entry))
            except ValidationError as exc:
                # Shape only: the text of an entry can hold the names and emails of people.
                logger.warning(
                    'chat.tool_call_dropped',
                    entry_type=type(entry).__name__,
                    keys=sorted(entry) if isinstance(entry, dict) else None,
                    fields=sorted({str(e['loc'][0]) for e in exc.errors() if e['loc']}),
                    errors=sorted({e['type'] for e in exc.errors()}),
                )
        return calls

    return BeforeValidator(read)


ToolCalls = Annotated[list[ToolCallRecord] | None, _readable(ToolCallRecord)]
AdminToolCalls = Annotated[list[AdminToolCallRecord] | None, _readable(AdminToolCallRecord)]
