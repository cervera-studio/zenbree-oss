"""The user's interface language, which every prompt that writes text the user reads names (#572).

Without it the model guesses from what it reads: an English site, German markets or English tool results."""

from __future__ import annotations

from app.core.locale import DEFAULT_LOCALE, Locale, stored_locale

_NAMES: dict[Locale, str] = {'en': 'English', 'it': 'Italian', 'es': 'Spanish', 'fr': 'French'}


def _filters(language: str) -> str:
    """The one exception to the user's language. Saying only that filters are English made DeepSeek write the
    countries of Italian personas and segments in English too ("in Italy o Spain", demo QA auth-3 and settings-6)."""
    return (
        "Only search filter values follow the search vendor and the target market, not the user's language: the "
        "inputs of a search tool, a segment's criteria and a profile's markets take English country names and "
        'industries, and job titles as LinkedIn profiles in the target market write them: in its language and in '
        f'English where they use it. In the sentences the user reads (answers, questions, the brief, profile and '
        f'segment text), write country names and industries in {language}, never copied from a filter.'
    )


def user_locale(conn, user_id: str) -> Locale:
    row = conn.execute('SELECT locale FROM "user" WHERE id = %s', (user_id,)).fetchone()
    return stored_locale(row['locale']) if row else DEFAULT_LOCALE


def save_locale(conn, user_id: str, locale: Locale) -> None:
    conn.execute('UPDATE "user" SET locale = %s WHERE id = %s AND locale <> %s', (locale, user_id, locale))


def language_instruction(locale: Locale) -> str:
    language = _NAMES[locale]
    # Filters stay in the form the search vendors take: an Italian country name would match nothing.
    return (
        f"# Language\nThe user's interface is in {language}. Write all text the user reads in {language}: answers, "
        'questions and options, the brief shown beside the chat, and the names, labels and descriptions of lists, '
        f'columns, plan stages and segments. Keep to {language} whatever the language of the website, the business '
        f'profile, the market or the tool results. {_filters(language)}'
    )


def language_reminder(locale: Locale) -> str:
    """The last line of a prompt whose material may be in another language. With the instruction in the system prompt
    only, DeepSeek wrote a Spanish or French user's profile in the language of their Italian description (#699).

    Measured with DeepSeek, 10 runs each: "write every field in Italian" turned the personas of 9 Italian profiles in
    10 into objects and the segments' job titles all Italian, so it names the shape and the filters' rules too."""
    language = _NAMES[locale]
    return (
        f'Write the text in {language}, whatever the language of the material above, and keep the JSON shape asked '
        f'for: a list of sentences stays a list of strings. {_filters(language)}'
    )
