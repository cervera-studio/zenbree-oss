"""The state of a person row's email and phone lookup (#659), read in one place.

A lookup stores its outcome in the row's cells (``email`` + ``email_status``, ``phone`` + ``phone_status``), and a
turn's charge on them in ``row_charges`` (``app.billing.row_charges``, which owns that record). This module is the
only other reader of those values: the API sends the typed ``Lookups`` on every row (so /openapi.json and the web's
generated types carry the states), the Python callers decide with ``deliverable``, and the SQL callers use the
predicates below, which ``tests/test_lookups_sql.py`` checks against ``email_lookup`` for every stored value."""

import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, Field

from app.billing.row_charges import PENDING_KEYS_SQL, refunded_sql
from app.core.errors_http import api_error
from app.lists.lookup_claims import CLAIMED_KEYS_SQL

# What the verifier says of a found address (``app.people.email_verification.email_status``).
EmailVerdict = Literal['verified', 'catch_all', 'mx_ok', 'unknown', 'invalid']
EMAIL_VERDICTS: tuple[EmailVerdict, ...] = get_args(EmailVerdict)
# Stored by a lookup that got no answer (a source failed, or none could be asked): the row is searched again.
FAILED = 'failed'
NOT_FOUND = 'not_found'
# ``phone_status`` of a found number, as the contact adapters name it.
REVEALED = 'revealed'


_STATUSES = {'email': {*EMAIL_VERDICTS, NOT_FOUND, FAILED}, 'phone': {REVEALED, NOT_FOUND, FAILED}}


def import_statuses(cells: dict[str, Any]) -> None:
    """Normalise an imported row's ``email_status`` / ``phone_status`` in place, as migration
    ``20261012152552_lookup_status.sql`` did for the stored ones: a known value lower-cased, any other dropped next
    to its address / number and ``not_found`` without one."""
    for key, known in _STATUSES.items():
        status = cells.get(f'{key}_status')
        if status is None:
            continue
        status = status.strip().lower()
        if status in known:
            cells[f'{key}_status'] = status
        elif cells.get(key):
            del cells[f'{key}_status']
        else:
            cells[f'{key}_status'] = NOT_FOUND


def _same(key: str, new: Any, old: Any) -> bool:
    """A re-typed address (case, spaces) or number (formatting) is the value the status was stored for."""
    if key == 'email':
        return str(new).strip().lower() == str(old or '').strip().lower()
    digits = [re.sub(r'\D', '', str(v or '')) for v in (new, old)]
    if all(digits):
        return digits[0] == digits[1]
    return str(new).strip() == str(old or '').strip()


def patch_statuses(conn, list_id, row_id, patch: dict) -> dict:
    """Lock the row and return ``patch`` (a hand edit's cells) with its ``email_status`` / ``phone_status`` normalised
    as an import's are, read against the address / number the row will hold after the edit. A status dropped as unknown
    leaves the stored one. A new non-empty address / number without its status in the same edit clears the stored
    status, which described the old value (a hand-typed phone must not keep ``not_found``)."""
    stored = conn.execute(
        'SELECT cells FROM list_rows WHERE id = %s AND list_id = %s FOR UPDATE', (row_id, list_id)
    ).fetchone()
    if not stored:
        raise api_error(404, 'ROW.NOT_FOUND', 'row not found')
    patch = patch | {
        f'{k}_status': None
        for k in ('email', 'phone')
        if patch.get(k)
        and not _same(k, patch[k], stored['cells'].get(k))
        and f'{k}_status' not in patch
        and stored['cells'].get(f'{k}_status')
    }
    statuses = [k for k in ('email_status', 'phone_status') if k in patch]
    if not statuses:
        return patch
    # A null status stays null (it clears the status); a null address / number is empty, not the text 'None'.
    merged = {**stored['cells'], **patch}
    seen: dict[str, Any] = {k: '' if merged.get(k) is None else str(merged[k]) for k in ('email', 'phone')}
    seen |= {k: None if patch[k] is None else str(patch[k]) for k in statuses}
    import_statuses(seen)
    return {k: v for k, v in patch.items() if k not in statuses} | {k: seen[k] for k in statuses if k in seen}


class EmailFound(BaseModel):
    state: Literal['found']
    verdict: EmailVerdict


class PhoneFound(BaseModel):
    state: Literal['found']


class Unfound(BaseModel):
    """``searching``: a live turn has this lookup open, either asked and not yet answered (a claim,
    ``app.lists.lookup_claims``) or answered and held while the charge's hold is reserved (``row_view``). ``refunded``:
    the turn took it back as undelivered. ``failed``: no source answered, so a later search asks again."""

    state: Literal['not_searched', 'searching', 'not_found', 'failed', 'refunded']


EmailLookup = Annotated[EmailFound | Unfound, Field(discriminator='state')]
PhoneLookup = Annotated[PhoneFound | Unfound, Field(discriminator='state')]


class Lookups(BaseModel):
    email: EmailLookup
    phone: PhoneLookup


# What a turn says of a row's email or phone cell, as ``CHARGES_SQL`` reads it: searching it (a claim of a live
# turn), holding it while the charge's hold is reserved, or taken back by its turn.
Charge = Literal['searching', 'refunded']
Charges = Mapping[str, Charge]
# On ``list_rows r``: the ``Charges`` of its email and phone cells.
CHARGES_SQL = (
    'jsonb_strip_nulls(jsonb_build_object('
    + ', '.join(
        f"'{key}', CASE WHEN '{key}' = ANY({PENDING_KEYS_SQL}) OR '{key}' = ANY({CLAIMED_KEYS_SQL}) THEN 'searching'"
        f" WHEN {refunded_sql(key)} THEN 'refunded' END"
        for key in ('email', 'phone')
    )
    + '))'
)


def _unfound(key: str, status: Any, charges: Charges) -> Unfound:
    if charges.get(key) == 'searching':
        return Unfound(state='searching')
    # A refund takes its status back, so a verdict here is a later search's answer; a failure keeps the refund open.
    if status and status != FAILED:
        return Unfound(state='not_found')
    if charges.get(key) == 'refunded':
        return Unfound(state='refunded')
    return Unfound(state='failed' if status == FAILED else 'not_searched')


def email_lookup(cells: Mapping, charges: Charges) -> EmailFound | Unfound:
    """``cells`` as the reader sees them (``row_view.VISIBLE_CELLS_SQL``). An address with no verdict (typed by
    hand, imported from a CSV) is found and unknown."""
    status = cells.get('email_status')
    if cells.get('email'):
        return EmailFound(state='found', verdict=status if status in EMAIL_VERDICTS else 'unknown')
    return _unfound('email', status, charges)


def phone_lookup(cells: Mapping, charges: Charges) -> PhoneFound | Unfound:
    if cells.get('phone'):
        return PhoneFound(state='found')
    return _unfound('phone', cells.get('phone_status'), charges)


def deliverable(lookup: EmailFound | Unfound) -> bool:
    """An address a charge bills and a campaign sends to: found, and not rejected by the verifier."""
    return isinstance(lookup, EmailFound) and lookup.verdict != 'invalid'


def verified_email_sql(cells: str) -> str:
    """``email_lookup`` is found with the verdict ``verified``, on the cells expression ``cells``."""
    return f"coalesce({cells}->>'email', '') <> '' AND {cells}->>'email_status' IS NOT DISTINCT FROM 'verified'"


def deliverable_email_sql(cells: str) -> str:
    """``deliverable(email_lookup(...))`` on the cells expression ``cells``."""
    return f"coalesce({cells}->>'email', '') <> '' AND {cells}->>'email_status' IS DISTINCT FROM 'invalid'"


# On ``list_rows r``: ``email_lookup`` is ``not_searched`` or ``failed``, the rows an email batch takes. A pending
# email is in the raw cells, so no pending row is open, and a row another call is searching is not either.
OPEN_EMAIL_SQL = (
    f"coalesce(r.cells->>'email', '') = '' AND coalesce(r.cells->>'email_status', '{FAILED}') = '{FAILED}'"
    f" AND NOT {refunded_sql('email')} AND NOT 'email' = ANY({CLAIMED_KEYS_SQL})"
)
# On ``list_rows r``: the verdict of a found email, or ``not_found`` / ``failed`` as ``email_lookup`` reads them; NULL
# for a row not searched or refunded. The chat state reads it at the start of a turn, when no call of that chat holds
# a claim, so a row another job is searching keeps its stored outcome: checking claims would cost every unsearched row
# a subquery, and only a failed row pays for the refund one.
EMAIL_OUTCOME_SQL = (
    f"CASE WHEN coalesce(r.cells->>'email', '') <> '' THEN CASE WHEN r.cells->>'email_status' IN"
    f" ({', '.join(repr(v) for v in EMAIL_VERDICTS)}) THEN r.cells->>'email_status' ELSE 'unknown' END"
    f" WHEN r.cells->>'email_status' = '{FAILED}' THEN CASE WHEN {refunded_sql('email')} THEN NULL ELSE '{FAILED}' END"
    f" WHEN r.cells->>'email_status' IS NOT NULL THEN '{NOT_FOUND}' END"
)
# Rows never searched before the ones whose search failed, so a lasting outage cannot fill every batch (#506).
FAILED_EMAIL_LAST_SQL = f"r.cells->>'email_status' IS NOT DISTINCT FROM '{FAILED}'"
