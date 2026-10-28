from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.core.errors import is_err
from app.core.mail_threading import reply_subject
from app.outreach import pool_state, sending
from app.outreach.domain.campaign import MAX_STEPS, Channel
from app.outreach.fields import CampaignField, UncheckedFields, load_inputs, missing_in_steps, resolve
from app.outreach.variants import Variant, allocating, copy_to_fill, has_test, sequence


class PreviewTarget(BaseModel):
    first_name: str
    last_name: str
    company_name: str
    role: str
    email: str | None
    linkedin_url: str


class PreviewStep(BaseModel):
    step: int
    channel: Channel
    subject: str
    body: str
    error: str | None
    sign_off: str
    footer: str


class PreviewItem(BaseModel):
    target: PreviewTarget
    steps: list[PreviewStep]
    # Variant B's steps (#934), A's copy where a step has no B; null when the campaign has no test.
    steps_b: list[PreviewStep] | None
    variant: Variant | None
    missing: list[str]


class Preview(BaseModel):
    items: list[PreviewItem]


class PreviewCopyIn(BaseModel):
    subject: str = Field(default='', max_length=2000)
    body: str = Field(default='', max_length=20000)


class PreviewStepIn(BaseModel):
    """A step as typed, not yet saved: lenient, since half-written steps are what the builder previews."""

    channel: Channel
    # Bounded for the request only; copy over the send limits comes back as COPY_TOO_LONG.
    subject: str = Field(default='', max_length=2000)
    body: str = Field(default='', max_length=20000)
    threaded: bool = False
    b: PreviewCopyIn | None = None


class LivePreviewIn(BaseModel):
    steps: list[PreviewStepIn] = Field(max_length=MAX_STEPS)
    fields: UncheckedFields | None = None
    limit: int = Field(default=10, ge=1, le=10)


def _footer(body: str, sign_off: str) -> str:
    # ponytail: the preview never mints the per-person unsubscribe page link, so its footer lacks the
    # «Oppure: <url>» sentence the real email carries.
    return '' if sending.carries_footer(sending.with_sign_off(body, sign_off)) else sending.opt_out_footer()


def _body_limit(channel: str) -> int:
    return sending.NOTE_MAX_PREMIUM if channel == 'linkedin_connect' else sending.MAX_BODY


def render_steps(steps: list[dict], person: dict, values: dict[str, str], sign_off: str = '') -> list[dict]:
    """Each step as the dispatcher would claim it for this person; ``error`` instead of copy it would refuse."""
    out: list[dict] = []
    for s in steps:
        item = {'step': s['step'], 'channel': s['channel'], 'subject': '', 'body': '', 'error': None}
        item.update(sign_off='', footer='')
        subject = sending.render_values(s['subject'], values)
        body = sending.render_values(s['body'], values)
        if s['channel'] == 'email' and sending.single_address(person.get('email')) is None:
            item['error'] = 'INVALID_EMAIL' if (person.get('email') or '').strip() else 'NO_EMAIL'
        elif is_err(subject):
            item['error'] = subject.error.code
        elif is_err(body):
            item['error'] = body.error.code
        elif s.get('threaded') and (root := sending.thread_root(steps, s['step'])):
            # A follow-up whose root cannot go out is skipped by the dispatcher (NO_THREAD).
            if out[root - 1]['error']:
                item['error'] = 'NO_THREAD'
            else:
                item.update(subject=reply_subject(out[root - 1]['subject']), body=body.value)
        else:
            item.update(subject=subject.value, body=body.value)
        if s['channel'] == 'email' and not item['error']:
            item.update(sign_off=sign_off.strip(), footer=_footer(item['body'], sign_off))
        if len(item['subject']) > sending.MAX_SUBJECT or len(item['body']) > _body_limit(s['channel']):
            item.update(subject='', body='', error='COPY_TOO_LONG')
        out.append(item)
    return out


def preview_items(conn, c: dict, steps: list[dict], fields: list[CampaignField], limit: int) -> list[dict]:
    people = conn.execute(
        'SELECT id::text AS id, sender_id, sender_pinned_at, status, variant, first_name, last_name, company_name, role,'
        " email, linkedin_url FROM outreach_campaign_targets WHERE campaign_id = %s AND status IN ('draft', 'active')"
        ' ORDER BY created_at LIMIT %s',
        (c['id'], limit),
    ).fetchall()
    inputs = load_inputs(conn, c, [p['id'] for p in people])
    pool = pool_state.accounts(conn, c, datetime.now(UTC))
    test, testing = has_test(steps), allocating({**c, 'steps': steps})
    items = []
    for p in people:
        mb = pool_state.sender_of(pool, p)
        resolved = resolve(fields, p, inputs.get(p['id']))
        target = {k: v for k, v in p.items() if k not in ('id', 'sender_id', 'sender_pinned_at', 'status', 'variant')}
        sign_off = (mb or {}).get('sign_off') or ''
        a = render_steps(sequence(steps, 'a'), p, resolved.values, sign_off)
        b = render_steps(sequence(steps, 'b'), p, resolved.values, sign_off) if test else None
        copy = copy_to_fill(steps, p['variant'], joins_test=testing and p['status'] == 'draft')
        missing = missing_in_steps(resolved.missing, copy)
        items.append({'target': target, 'steps': a, 'steps_b': b, 'variant': p['variant'], 'missing': missing})
    return items
