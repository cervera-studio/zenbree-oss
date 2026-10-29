"""Minimal M2 step contract adapted from the earlier implementation outreach/domain/campaign.py.

Validate at every future campaign write boundary. Refuse invalid input rather
than dropping, renumbering or rewriting messages that a person may have approved.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

MAX_STEPS = 12
# hubspot_task (#123): nothing is sent; a HubSpot task with the drafted message goes to the account's owner.
Channel = Literal['email', 'linkedin_connect', 'linkedin_message', 'hubspot_task']


class StepCopy(BaseModel):
    """Variant B's subject and body of a step (#934); everything else about the step is shared."""

    model_config = ConfigDict(extra='forbid', strict=True)

    subject: str = Field(default='', max_length=300)
    body: str = Field(max_length=4000)


class CampaignStep(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    step: int = Field(ge=1, le=MAX_STEPS)
    channel: Channel
    subject: str = Field(default='', max_length=300)
    body: str = Field(max_length=4000)
    send_after_days: int = Field(ge=0)
    personalised: str = Field(default='', max_length=400)
    threaded: bool = False
    b: StepCopy | None = None


class StepError(ValueError):
    """A rule one step breaks: ``step`` lets the builder name it."""

    def __init__(self, step: int, message: str, code: str = 'CAMPAIGN.INVALID_STEPS'):
        super().__init__(message)
        self.step = step
        self.code = code


def validate_steps(raw: object) -> list[dict]:
    """Return JSON-ready steps, retaining the order/copy; [] is a valid draft."""
    steps: list[CampaignStep] = TypeAdapter(Annotated[list[CampaignStep], Field(max_length=MAX_STEPS)]).validate_python(
        raw, strict=True
    )
    floor, last_dm = 0, -1
    connected, email_root = False, False
    for number, step in enumerate(steps, 1):
        if step.step != number or step.send_after_days < floor:
            raise StepError(number, 'Steps must be numbered 1..n with non-decreasing delays')
        if step.channel != 'linkedin_connect' and not step.body.strip():
            raise StepError(number, 'Every message requires a body except a LinkedIn invitation')
        if step.personalised and step.personalised not in step.body:
            raise StepError(number, 'Personalised text must occur in the body')
        if step.channel == 'email':
            if step.threaded:
                if not email_root or step.subject:
                    raise StepError(number, 'Threaded email needs an earlier email subject and no new subject')
            elif not step.subject.strip():
                raise StepError(number, 'Email requires a subject')
            else:
                email_root = True
        elif step.threaded or (step.subject and step.channel != 'hubspot_task'):
            raise StepError(number, 'Only an email can be threaded, and only an email or a task has a subject')
        if step.channel == 'linkedin_message':
            if not connected:
                raise StepError(
                    number, 'A LinkedIn message requires an earlier LinkedIn invitation', 'CAMPAIGN.LINKEDIN_NO_INVITE'
                )
            if step.send_after_days <= last_dm:
                raise StepError(
                    number,
                    'A LinkedIn message must come at least a day after the previous one',
                    'CAMPAIGN.LINKEDIN_DM_DELAY',
                )
            last_dm = step.send_after_days
        connected |= step.channel == 'linkedin_connect'
        floor = step.send_after_days
    # No `b` key on a step without one: a campaign with no test stores the same JSON as before #934.
    return [step.model_dump(exclude={'b'} if step.b is None else None) for step in steps]
