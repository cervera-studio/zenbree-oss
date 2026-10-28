"""A first list's chat opening, built on the server from an onboarding segment (#70): what the chat shows, its
title and the brief the agent reads. Onboarding builds it from the segment the user picked, a Home welcome card from
the stored segment its key names, so no client writes the text the agent reads."""

from __future__ import annotations

from typing import NamedTuple

import psycopg
from pydantic import ValidationError

from app.core.locale import Locale
from app.core.segment_key import segment_key
from app.onboarding.study import Profile, Segment, first_list_brief, first_list_message


class FirstList(NamedTuple):
    message: str
    title: str
    brief: str


def first_list(profile: Profile, segment: Segment, fallback: Locale) -> FirstList:
    return FirstList(first_list_message(segment, fallback), segment.name, first_list_brief(profile, segment, fallback))


def stored_first_list(conn: psycopg.Connection[dict], project_id: str, key: str, fallback: Locale) -> FirstList | None:
    """The opening of the project's stored segment whose ``segment_key`` is ``key``, the key a welcome card carries;
    None when no stored segment has it or it no longer parses."""
    row = conn.execute(
        'SELECT profile, segments FROM business_profiles WHERE project_id = %s', (project_id,)
    ).fetchone()
    raw = next((s for s in (row and row['segments']) or [] if segment_key(s) == key), None)
    if row is None or raw is None:
        return None
    try:
        return first_list(Profile.model_validate(row['profile'] or {}), Segment.model_validate(raw), fallback)
    except ValidationError:
        return None
