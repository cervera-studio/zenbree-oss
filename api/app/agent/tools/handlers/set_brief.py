from app.agent.tools.registry import ToolContext, ToolDef, ToolResult, register

FIELDS = ('who', 'companies', 'where', 'signal')
EMAIL_CHOICES = ('required', 'if_found', 'not_needed')
MAX_CHIPS = 6
CHIP_CHARS = 60


def _chips(raw: object) -> list[str]:
    seen: dict[str, str] = {}
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str) and (chip := item.strip()[:CHIP_CHARS]):
            seen.setdefault(chip.casefold(), chip)
    return list(seen.values())[:MAX_CHIPS]


async def set_brief(args: dict, ctx: ToolContext) -> ToolResult:
    brief: dict = {field: _chips(args.get(field)) for field in FIELDS}
    email = args.get('email')
    brief['email'] = email if email in EMAIL_CHOICES else None
    return ToolResult(ok=True, data=brief, brief=brief if any(brief.values()) else None)


register(
    ToolDef(
        name='set_brief',
        description=(
            "Record what you understood of the user's brief, shown to them beside the chat. Call it right after "
            'the first search of a new brief and again whenever the target changes; each call replaces the last. '
            "Short noun phrases in the user's interface language, at most 6 per field. They are text the user "
            'reads, not search filters: never copy the English or vendor form of a search input (a country, an '
            'industry, a job title); write it as the user would in their language. '
            'who: the roles to reach (e.g. "CEO"); companies: the kind and size of company; where: places; '
            'signal: a buying signal to look for (e.g. "Hiring SDRs, 30 days"). email: "required" when only rows '
            'with an email are wanted, "if_found", "not_needed", or omit it while it is undecided. '
            'Omit a field you do not know; never guess.'
        ),
        input_schema={
            'type': 'object',
            'properties': {
                **{f: {'type': 'array', 'items': {'type': 'string'}, 'maxItems': MAX_CHIPS} for f in FIELDS},
                'email': {'type': 'string', 'enum': list(EMAIL_CHOICES)},
            },
        },
        handler=set_brief,
    )
)
