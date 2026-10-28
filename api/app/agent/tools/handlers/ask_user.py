"""Pause the turn for a question; the job ends ``needs_input`` and the user's
reply is the next chat turn (decision on #40, instead of the earlier implementation's server-side gate)."""

from app.agent.tools.registry import ToolContext, ToolDef, ToolResult, register

MAX_OPTIONS = 6


def _text(raw: object, limit: int) -> str:
    return raw.strip()[:limit] if isinstance(raw, str) else ''


def _option(raw: object) -> dict | None:
    label, description = (raw.get('label'), raw.get('description')) if isinstance(raw, dict) else (raw, None)
    label = _text(label, 200)
    return {'label': label, 'description': _text(description, 300) or None} if label else None


async def ask_user(args: dict, ctx: ToolContext) -> ToolResult:
    question = _text(args.get('question'), 1000)
    if not question:
        return ToolResult(ok=False, error='question is required')
    raw = args.get('options')
    options: dict[str, dict] = {}
    for option in map(_option, raw if isinstance(raw, list) else []):
        if option:
            options.setdefault(option['label'].casefold(), option)
    return ToolResult(ok=True, data={'question': question, 'options': list(options.values())[:MAX_OPTIONS]})


register(
    ToolDef(
        name='ask_user',
        description=(
            'Ask the user one question and stop until they answer. Use only when a choice that changes the result '
            'or its cost is genuinely ambiguous (e.g. target country, companies vs people); otherwise pick a '
            'sensible default and proceed. Offer 2-4 options when the answer is a choice, each with a short label and one '
            "line saying what it means for the result or its cost. Write the question and options in the user's "
            'interface language. Do not repeat the question in your text.'
        ),
        input_schema={
            'type': 'object',
            'properties': {
                'question': {'type': 'string'},
                'options': {
                    'type': 'array',
                    'maxItems': MAX_OPTIONS,
                    'items': {
                        'type': 'object',
                        'properties': {'label': {'type': 'string'}, 'description': {'type': 'string'}},
                        'required': ['label'],
                    },
                },
            },
            'required': ['question'],
        },
        handler=ask_user,
    )
)
