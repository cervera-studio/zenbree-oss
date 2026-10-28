"""Onboarding (E5): study the company, edit the profile, pick a segment, start the first list.

The web gate (``app/(app)/layout.tsx``) sends a user here while the active project ``needs_onboarding``.
The profile is the project's (#307): a new project (client) runs this for itself. A project that already
has a chat skips it, so projects older than onboarding are never forced through it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from app.agent.chat_request import ChatRequest
from app.agent.language import user_locale
from app.agent.routes import _drop_unqueued_chat, _enqueue, _start_turn
from app.auth.middleware import CurrentUser, current_user
from app.core.db import get_conn
from app.core.errors_http import api_error
from app.core.event_stream import EventStream
from app.core.locale import Locale
from app.exclusions.rules import profile_rule
from app.onboarding.first_list import first_list
from app.onboarding.study import (
    Profile,
    Segment,
    propose_segments,
    reply_unbilled,
)
from app.onboarding.study_stream import StudyIn, study_stream
from app.onboarding.support import meter, refund_free_run, save, use_free_run
from app.projects.routes import current_project

router = APIRouter(prefix='/v1/onboarding', tags=['onboarding'])
Auth = Depends(current_user)
Project = Depends(current_project)


class Onboarding(BaseModel):
    website: str | None
    description: str | None
    profile: Profile | None
    segments: list[Segment]
    completed_at: datetime | None
    needs_onboarding: bool


class SavedBusinessProfile(BaseModel):
    profile: Profile


class SegmentsIn(BaseModel):
    locale: Locale | None = None  # the language the user sees; None = the one saved on the user


class Segments(BaseModel):
    segments: list[Segment]


class Completed(BaseModel):
    session_id: UUID | None


def _locale(user_id: str, seen: Locale | None) -> Locale:
    """The language of this call's text: the one the web names from its cookie, else the saved one (#583).

    Never saved: only an explicit choice writes ``"user".locale`` (#795)."""
    if seen:
        return seen
    with get_conn() as conn:
        return user_locale(conn, user_id)


def _state(project_id: str) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            'SELECT website, description, profile, segments, completed_at FROM business_profiles WHERE project_id = %s',
            (project_id,),
        ).fetchone() or {'website': None, 'description': None, 'profile': None, 'segments': None, 'completed_at': None}
        has_chats = conn.execute('SELECT 1 FROM chat_sessions WHERE project_id = %s LIMIT 1', (project_id,)).fetchone()
    return {**row, 'segments': row['segments'] or [], 'needs_onboarding': not row['completed_at'] and not has_chats}


@router.get('', response_model=Onboarding)
def get_onboarding(project_id: str = Project) -> dict:
    state = _state(project_id)
    # Through the model, so segments stored before a field existed (locale #587, signal_hint #191) carry its default.
    return {**state, 'segments': [Segment.model_validate(s).model_dump() for s in state['segments']]}


@router.post(
    '/profile',
    response_class=EventStream,
    responses={
        422: {'description': 'Neither a website nor a description'},
        429: {'description': 'The organization used its free runs (ONBOARDING.LIMIT)'},
    },
)
async def study(body: StudyIn, request: Request, user: CurrentUser = Auth, project_id: str = Project) -> EventStream:
    """Build the business profile from the website, or the description when there is none (#67), as SSE (#350):
    ``page``, ``drafting``, ``offer``, then ``profile`` or ``error``."""
    await asyncio.to_thread(use_free_run, user.org_id)
    # ponytail: the run is counted before the generator first iterates, so a client that aborts in that window loses
    # one free run with no LLM call. Accepted: the 429 has to stay a plain HTTP error before the stream starts.
    locale = await asyncio.to_thread(_locale, user.id, body.locale)
    return EventStream(study_stream(body, user, project_id, locale, request.receive))


@router.put('/profile', response_model=SavedBusinessProfile)
def save_profile(profile: Profile, user: CurrentUser = Auth, project_id: str = Project) -> dict:
    """The confirmed or edited profile of the active project (#69). The agent reads it from there (#307), not
    from the user's Memory: a profile is one client's, Memory is the user's across clients."""
    save(project_id, user.id, profile=Jsonb(profile.model_dump()))
    _sync_exclusions(project_id, user.id, profile.exclusions)
    _name_default_project(project_id, profile.company_name)
    return {'profile': profile.model_dump()}


def _name_default_project(project_id: str, company_name: str) -> None:
    """The client still carrying the placeholder name takes the company's; a name the user chose stays."""
    if name := company_name.strip()[:120]:
        with get_conn() as conn:
            conn.execute(
                'UPDATE projects SET name = %s, name_is_placeholder = false WHERE id = %s AND name_is_placeholder',
                (name, project_id),
            )


def _sync_exclusions(project_id: str, user_id: str, lines: list[str]) -> None:
    """Carry the profile's exclusion lines that changed since the last sync into Exclusions (#193).

    Lines that name an email, domain, company or LinkedIn URL become rules tagged
    ``source = 'onboarding'``; the rest stay brief-only text. Only the diff is applied:
    an unchanged line never re-creates a rule the user deleted, and a removed line
    deletes only an onboarding rule, never one the user owns (``source`` NULL).
    """
    with get_conn() as conn:
        # The profile row lock: concurrent saves take turns.
        row = conn.execute(
            'SELECT exclusion_lines FROM business_profiles WHERE project_id = %s FOR UPDATE', (project_id,)
        ).fetchone()
        synced = (row and row['exclusion_lines']) or []
        if lines == synced:
            return
        before = {r for line in synced if (r := profile_rule(line))}
        after = {r for line in lines if (r := profile_rule(line))}
        for sql, rules in (
            (
                "DELETE FROM exclusions WHERE project_id = %s AND source = 'onboarding' AND origin = 'profile'"
                ' AND (kind, value) IN (SELECT * FROM unnest(%s::text[], %s::text[]))',
                before - after,
            ),
            (
                'INSERT INTO exclusions (project_id, user_id, kind, value, source, origin)'
                " SELECT %s, %s, k, v, 'onboarding', 'profile'"
                ' FROM unnest(%s::text[], %s::text[]) AS t(k, v) ON CONFLICT DO NOTHING',
                after - before,
            ),
        ):
            if rules:
                kinds, values = zip(*rules, strict=True)
                ids = (project_id,) if sql.startswith('DELETE') else (project_id, user_id)
                conn.execute(sql, (*ids, list(kinds), list(values)))
        conn.execute('UPDATE business_profiles SET exclusion_lines = %s WHERE project_id = %s', (lines, project_id))


@router.post('/segments', response_model=Segments)
async def segments(body: SegmentsIn | None = None, user: CurrentUser = Auth, project_id: str = Project) -> dict:
    """Who / problem / outcome segments from the saved profile (#68)."""
    state = await asyncio.to_thread(_state, project_id)
    if not state['profile']:
        raise api_error(409, 'ONBOARDING.NO_PROFILE', 'Create the business profile first')
    await asyncio.to_thread(use_free_run, user.org_id)
    meter(user)
    locale = await asyncio.to_thread(_locale, user.id, body.locale if body else None)
    try:
        found = await propose_segments(Profile.model_validate(state['profile']), locale)
    except Exception as e:
        if reply_unbilled(e):
            await asyncio.to_thread(refund_free_run, user.org_id)
        raise api_error(502, 'ONBOARDING.LLM_FAILED', 'Could not propose segments, try again') from e
    out = [s.model_dump() for s in found]
    await asyncio.to_thread(save, project_id, user.id, segments=Jsonb(out))
    return {'segments': out}


class OnboardingCompleteIn(BaseModel):
    segment: Segment | None = None  # None = skip: finish onboarding without a first list
    locale: Locale | None = None  # the language the user sees; None = the one saved on the user


@router.post('/complete', response_model=Completed)
def complete(body: OnboardingCompleteIn, user: CurrentUser = Auth, project_id: str = Project) -> dict:
    """Finish the project's onboarding; with a segment, start its first list as a chat turn (#70)."""
    session_id = None
    locale = _locale(user.id, body.locale)
    if body.segment:
        opening = first_list(Profile.model_validate(_state(project_id)['profile'] or {}), body.segment, locale)
        session_id, job_id = _start_turn(
            user,
            ChatRequest(message=opening.message, project_id=UUID(project_id), locale=locale),
            opening=opening,
        )
        try:
            _enqueue(job_id)
        except HTTPException:
            _drop_unqueued_chat(session_id)
            raise
    with get_conn() as conn:
        conn.execute(
            'INSERT INTO business_profiles (project_id, user_id, completed_at) VALUES (%s, %s, now())'
            ' ON CONFLICT (project_id) DO UPDATE SET'
            ' completed_at = COALESCE(business_profiles.completed_at, now()), updated_at = now()',
            (project_id, user.id),
        )
    return {'session_id': session_id}
