"""``POST /v1/onboarding/profile`` as SSE (#350): the study's progress frames, and its cancel by disconnect."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

import structlog
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, model_validator
from starlette.types import Receive

from app.agent.language import Locale
from app.auth.middleware import CurrentUser
from app.core.scope import ActorGone
from app.onboarding.study import (
    Drafting,
    OfferFound,
    PageRead,
    SiteUnreadableError,
    Studied,
    StudyEvent,
    normalize_website,
    reply_unbilled,
    study_company,
)
from app.onboarding.support import meter, refund_free_run, save_study

logger = structlog.get_logger()


class StudyIn(BaseModel):
    website: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=4000)
    locale: Locale | None = None  # the language the user sees; None = the one saved on the user

    @model_validator(mode='after')
    def _one_source(self) -> StudyIn:
        self.website = (self.website or '').strip() or None
        self.description = (self.description or '').strip() or None
        if not self.website and not self.description:
            raise ValueError('website or description is required')
        return self


def _frame(event: str, data: dict) -> str:
    return f'event: {event}\ndata: {json.dumps(data)}\n\n'


def _study_frame(event: StudyEvent) -> str:
    match event:
        case PageRead(url):
            return _frame('page', {'url': url})
        case Drafting():
            return _frame('drafting', {})
        case OfferFound(summary):
            return _frame('offer', {'summary': summary})
        case Studied(profile, pages):
            return _frame('profile', {'profile': profile.model_dump(), 'pages': pages})


async def _run_study(
    body: StudyIn, user: CurrentUser, project_id: str, locale: Locale, frames: asyncio.Queue[str | None]
) -> None:
    """The free run is given back only before ``drafting``: a cancel after the LLM call started still counts."""
    meter(user)
    counted = False
    try:
        async for event in study_company(body.website, body.description, locale):
            counted = counted or isinstance(event, Drafting)
            if isinstance(event, Studied):
                website = normalize_website(body.website) if body.website else None
                await asyncio.to_thread(
                    save_study, project_id, user.id, website, body.description, Jsonb(event.profile.model_dump())
                )
            await frames.put(_study_frame(event))
    except asyncio.CancelledError:
        if not counted:
            await asyncio.to_thread(refund_free_run, user.org_id)
        raise
    except Exception as e:
        if not counted or reply_unbilled(e):
            await asyncio.to_thread(refund_free_run, user.org_id)
        if isinstance(e, SiteUnreadableError):
            error = {
                'code': 'ONBOARDING.SITE_UNREADABLE',
                'message': 'The website could not be read; describe the business',
            }
        else:
            logger.warning('onboarding_study_failed', exc_info=True)
            error = {'code': 'ONBOARDING.STUDY_FAILED', 'message': 'Could not analyse the business, try again'}
        await frames.put(_frame('error', error))
    finally:
        frames.put_nowait(None)


async def _cancel_on_disconnect(receive: Receive, run: asyncio.Task[None]) -> None:
    while (await receive())['type'] != 'http.disconnect':
        pass
    run.cancel()


async def study_stream(
    body: StudyIn, user: CurrentUser, project_id: str, locale: Locale, receive: Receive
) -> AsyncGenerator[str]:
    """A server speaking ASGI 2.3 cancels this generator on disconnect; the watcher covers 2.4, which sees one only on send."""
    frames: asyncio.Queue[str | None] = asyncio.Queue()
    run = asyncio.create_task(_run_study(body, user, project_id, locale, frames))
    watcher = asyncio.create_task(_cancel_on_disconnect(receive, run))
    try:
        while (frame := await frames.get()) is not None:
            yield frame
        await asyncio.wait({run})
        # The member left while the study ran (its save is the one checkout): EventStream ends the stream with the
        # `error` frame. No refund: the save comes after the LLM call, which counts.
        if not run.cancelled() and isinstance(gone := run.exception(), ActorGone):
            raise gone
    finally:
        watcher.cancel()
        run.cancel()
        # Best effort: the cancelled scope may cancel this wait at once; the refund still commits, it runs in a thread.
        await asyncio.wait({run})
