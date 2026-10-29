"""What the chat state tells the agent about each list: rows, ratings and what the email lookups found, so a question
about what a list already holds is answered from it instead of a paid lookup (demo QA chat-12)."""

from __future__ import annotations

from app.lists.lookups import EMAIL_OUTCOME_SQL

# In the agent's words: SYSTEM_PROMPT keeps status values off the chat, and the model repeats what it reads here.
_EMAILS = {
    'verified': 'verified',
    'catch_all': 'on a domain that accepts any address (cannot be verified)',
    'found': 'found but not verified',
    'invalid': 'rejected by the verifier',
    'not_found': 'not found',
    'failed': 'lookup failed (can be retried)',
}
_UNVERIFIED = ('mx_ok', 'unknown')
_OUTCOMES = (*(o for o in _EMAILS if o != 'found'), *_UNVERIFIED)


def list_lines(conn, lists: dict[str, str]) -> list[str]:
    """One line per list of ``lists`` (entity to list id), counted in one query over the chat's lists."""
    counts = {
        str(c['list_id']): c
        for c in conn.execute(
            "SELECT list_id, count(*) AS n, count(*) FILTER (WHERE rating = 'good') AS good,"
            " count(*) FILTER (WHERE rating = 'bad') AS bad, "
            + ', '.join(f"count(*) FILTER (WHERE o = '{o}') AS {o}" for o in _OUTCOMES)
            + f' FROM (SELECT list_id, rating, {EMAIL_OUTCOME_SQL} AS o FROM list_rows r'
            ' WHERE list_id = ANY(%s::uuid[])) x GROUP BY list_id',
            (list(lists.values()),),
        )
    }
    return [f'- {entity} list {lid}: {_describe(entity, counts.get(lid))}' for entity, lid in lists.items()]


def _describe(entity: str, c: dict | None) -> str:
    if not c:
        return '0 rows'
    text = f'{c["n"]} rows'
    if c['good'] or c['bad']:
        text += f' ({c["good"]} rated good, {c["bad"]} bad: see list_row_feedback)'
    emails = {o: c[o] for o in _EMAILS if o != 'found'} | {'found': sum(c[o] for o in _UNVERIFIED)}
    searched = sum(emails.values())
    if entity != 'person' and not searched:
        return text
    parts = [f'{emails[o]} {words}' for o, words in _EMAILS.items() if emails[o]]
    if entity == 'person' and c['n'] > searched:
        parts.append(f'{c["n"] - searched} not searched yet')
    return f'{text}; emails: {", ".join(parts)}'
