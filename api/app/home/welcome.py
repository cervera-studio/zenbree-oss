"""The first-access Home (#210): what a user sees after onboarding until the checklist is done or hidden.

One SQL row of facts per user, then a pure `decide`. `None` is every "show the regular Home" case."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ValidationError

from app.core.db import get_conn
from app.core.locale import Locale
from app.core.segment_key import segment_key
from app.onboarding.study import Profile, Segment, first_list_message

StepKind = Literal['profile', 'list', 'mailbox']
STEP_ORDER: tuple[StepKind, ...] = ('profile', 'list', 'mailbox')
REQUIRED_STEPS: tuple[StepKind, ...] = ('profile', 'list')
_CARD_TITLES = 2


class WelcomeStep(BaseModel):
    kind: StepKind
    done: bool


class SegmentCard(BaseModel):
    key: str  # ``POST /v1/chat`` opens the segment's chat from it (``segment``)
    name: str
    titles: list[str]
    employee_range: str | None
    message: str  # the chat's first message; the agent reads the segment's brief in its place
    locale: Locale | None  # the language its name was written in (#646); None on segments stored before #643


class Welcome(BaseModel):
    company_name: str | None
    knows_from: Literal['website', 'description', 'none']
    segments: list[SegmentCard]
    steps: list[WelcomeStep]


class Facts(BaseModel):
    profile: dict | None
    segments: list[dict] | None
    website: str | None
    description: str | None
    has_memory: bool
    has_list: bool
    has_mailbox: bool


# The WHERE is the show rule's storage half: old accounts have no completed_at, and a closed row stays closed.
_FACTS_SQL = r"""
SELECT bp.profile, bp.segments, bp.website, bp.description,
       EXISTS (SELECT 1 FROM user_memory_doc m
                WHERE m.user_id = %(user)s AND m.body ~ '\S') AS has_memory,
       EXISTS (SELECT 1 FROM lists l WHERE l.project_id = bp.project_id AND l.deleted_at IS NULL
                  AND EXISTS (SELECT 1 FROM list_rows r WHERE r.list_id = l.id)) AS has_list,
       EXISTS (SELECT 1 FROM user_oauth_connections c
                WHERE c.id IN (SELECT sender_id FROM project_senders WHERE project_id = bp.project_id) AND c.provider IN ('google', 'microsoft')
                  AND c.status = 'connected') AS has_mailbox
  FROM business_profiles bp
 WHERE bp.project_id = %(project)s AND bp.completed_at IS NOT NULL AND bp.welcome_closed_at IS NULL
"""

_CLOSE_SQL = """
UPDATE business_profiles SET welcome_closed_at = now(), updated_at = now()
 WHERE project_id = %s AND welcome_closed_at IS NULL
"""


def _range(employee_ranges: list[str]) -> str | None:
    return employee_ranges[0].replace(',', '–') if employee_ranges else None


def _cards(profile: Profile, raw_segments: list[dict], locale: Locale) -> list[SegmentCard]:
    cards = []
    for raw in raw_segments:
        try:
            seg = Segment.model_validate(raw)
        except ValidationError:
            continue
        cards.append(
            SegmentCard(
                key=segment_key(raw),
                name=seg.name,
                titles=seg.criteria.titles[:_CARD_TITLES],
                employee_range=_range(seg.criteria.employee_ranges),
                message=first_list_message(seg, locale),
                locale=seg.locale,
            )
        )
    return cards


def decide(facts: Facts, locale: Locale) -> Welcome | None:
    done: dict[StepKind, bool] = {
        'profile': facts.profile is not None or facts.has_memory,
        'list': facts.has_list,
        'mailbox': facts.has_mailbox,
    }
    if all(done[k] for k in REQUIRED_STEPS):
        return None
    try:
        profile = Profile.model_validate(facts.profile) if facts.profile is not None else None
    except ValidationError:
        profile = None
    knows_from: Literal['website', 'description', 'none'] = (
        'none' if profile is None else 'website' if facts.website else 'description'
    )
    return Welcome(
        company_name=(profile.company_name or None) if profile else None,
        knows_from=knows_from,
        segments=_cards(profile, facts.segments or [], locale) if profile else [],
        steps=[WelcomeStep(kind=k, done=done[k]) for k in STEP_ORDER],
    )


def close_welcome(project_id: str) -> None:
    with get_conn() as conn:
        conn.execute(_CLOSE_SQL, (project_id,))


def load_welcome(user_id: str, project_id: str, locale: Locale) -> Welcome | None:
    """The welcome card of the active project (#307): its profile, lists and mailbox."""
    with get_conn() as conn:
        row = conn.execute(_FACTS_SQL, {'user': user_id, 'project': project_id}).fetchone()
    if row is None:
        return None
    welcome = decide(Facts.model_validate(row), locale)
    if welcome is None:
        close_welcome(project_id)
    return welcome
