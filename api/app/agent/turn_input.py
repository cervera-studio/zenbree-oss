"""What a turn reads before its first step: the language it writes in and the chat as the model sees it."""

from __future__ import annotations

import psycopg

from app.agent import funnel
from app.agent.language import user_locale
from app.agent.turn_start import chat_title
from app.core.locale import LOCALES, Locale


def turn_locale(conn: psycopg.Connection[dict], job_id: str, user_id: str) -> Locale:
    """The language the user saw when they sent the turn (``jobs.locale``), else the saved one."""
    row = conn.execute('SELECT locale FROM jobs WHERE id = %s', (job_id,)).fetchone()
    return row['locale'] if row and row['locale'] in LOCALES else user_locale(conn, user_id)


def latest_ask(msgs: list[dict]) -> str:
    """The name of a list a search creates without a plan: the user's latest message, cut like a chat title."""
    asked = [m['content'] for m in msgs if m['role'] == 'user']
    return chat_title(asked[-1]) if asked else ''


def with_brief(msgs: list[dict], brief: str | None) -> list[dict]:
    """``chat_sessions.brief`` in place of the chat's first user message, which showed the user only
    a summary."""
    first = next((i for i, m in enumerate(msgs) if m['role'] == 'user'), None)
    if not brief or first is None:
        return msgs
    return [*msgs[:first], {**msgs[first], 'content': brief}, *msgs[first + 1 :]]


def model_history(msgs: list[dict]) -> list[dict]:
    messages: list[dict] = []
    for m in msgs:
        content = funnel.with_question(m['content'], m['question'])
        if not content:
            continue
        if messages and messages[-1]['role'] == m['role']:
            messages[-1]['content'] += '\n\n' + content
        else:
            messages.append({'role': m['role'], 'content': content})
    return messages
