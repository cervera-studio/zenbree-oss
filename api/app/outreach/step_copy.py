from __future__ import annotations

from pydantic import ValidationError

from app.core.errors import is_err
from app.outreach import sending
from app.outreach.domain.campaign import StepError, validate_steps
from app.outreach.fields import CampaignField, resolve, sample_inputs
from app.outreach.variants import Variant, has_test, sequence

# Obviously invented and complete, so a test send judges the template, never a real person (the earlier implementation), and a step
# rendered against it shows an unknown token on write.
SAMPLE_CONTACT = {
    'first_name': 'Sample',
    'last_name': 'Contact',
    'company_name': 'Esempio S.r.l.',
    'role': 'Head of Operations',
    'email': 'sample@example.com',
}


class StepsRefused(ValueError):
    def __init__(self, code: str, message: str, step: int | None = None, variant: Variant | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        # The web names the step from these; the message is English for logs and API clients.
        self.step = step
        self.variant = variant  # 'b': the refusal is in variant B's copy of the step


def check_subject(subject: str, step: int | None = None, variant: Variant | None = None) -> None:
    # A line break cannot go in a header: the dispatcher would refuse the row and the person would stall.
    if '\r' in subject or '\n' in subject:
        where = ('variant b ' if variant else '') + (f'step {step} ' if step else '')
        message = f'{where}subject: a subject must be a single line'
        raise StepsRefused('CAMPAIGN.SUBJECT_LINE_BREAK', message, step, variant)


def checked_steps(
    raw: list[dict], *, agent_copy: bool = False, fields: list[CampaignField] | None = None
) -> list[dict]:
    """JSON-ready steps, or ``StepsRefused`` with the reason.

    A copy over the storage limits is refused here, not later at claim time (COPY_TOO_LONG).
    ``agent_copy``: also refuse an unknown ``{{token}}``, an unfilled ``[Name]`` and an invite note over
    LinkedIn's limit; the agent can invent them, and the builder's typed copy shows them in its preview.
    Variant B's copy passes the same checks as A's: a B person gets it on the same shape.

    ponytail: a field counts at its fallback's length, a required one at its token's. A person's own value is counted
    only in their preview and when their step is planned (a claim refused as COPY_TOO_LONG).
    """
    fields = fields or []
    steps = _checked(raw, agent_copy, fields, None)
    if has_test(steps):
        _checked(sequence(steps, 'b'), agent_copy, fields, 'b')
    return steps


def _checked(raw: list[dict], agent_copy: bool, fields: list[CampaignField], variant: Variant | None) -> list[dict]:
    fallbacks = {f.token: f.fallback for f in fields if f.fallback is not None}
    where = 'variant b ' if variant else ''
    for n, step in enumerate(raw, 1):
        for field, limit in (('subject', sending.MAX_SUBJECT), ('body', sending.MAX_BODY)):
            value = step.get(field)
            if isinstance(value, str) and (size := len(sending.fill_known(value, fallbacks))) > limit:
                message = f'{where}step {n} {field}: {size} characters, at most {limit}'
                raise StepsRefused('CAMPAIGN.COPY_TOO_LONG', message, n, variant)
        subject = step.get('subject')
        if isinstance(subject, str):
            check_subject(subject, n, variant)
    try:
        steps = validate_steps(raw)
    except ValidationError as e:
        first = e.errors()[0]
        loc = first['loc']
        at = loc[0] + 1 if loc and isinstance(loc[0], int) else None
        variant = 'b' if len(loc) > 1 and loc[1] == 'b' else variant
        message = f'{where}{".".join(map(str, loc))}: {first["msg"]}'
        raise StepsRefused('CAMPAIGN.INVALID_STEPS', message[:300], at, variant) from None
    except StepError as e:
        raise StepsRefused(e.code, f'{where}step {e.step}: {e}'[:300], e.step, variant) from None
    if agent_copy:
        sample = resolve(fields, SAMPLE_CONTACT, sample_inputs(fields)).values
        for step in steps:
            _check_copy(step, sample, fallbacks, variant)
    return steps


def _check_copy(step: dict, sample: dict[str, str], fallbacks: dict[str, str], variant: Variant | None) -> None:
    n, where = step['step'], 'variant b ' if variant else ''
    size = len(sending.fill_known(step['body'], fallbacks))
    if step['channel'] == 'linkedin_connect' and size > sending.NOTE_MAX_PREMIUM:
        raise StepsRefused(
            'CAMPAIGN.COPY_TOO_LONG',
            f'{where}step {n} body: an invite note is {size} characters, at most {sending.NOTE_MAX_PREMIUM}',
            n,
            variant,
        )
    for field in ('subject', 'body'):
        rendered = sending.render_values(step[field], sample)
        if is_err(rendered):
            raise StepsRefused(
                'CAMPAIGN.INVALID_STEPS', f'{where}step {n} {field}: {rendered.error.detail}', n, variant
            )
        if placeholder := sending.find_placeholder(rendered.value):
            raise StepsRefused(
                'CAMPAIGN.PLACEHOLDER', f'{where}step {n} {field}: fill or remove {placeholder}', n, variant
            )
