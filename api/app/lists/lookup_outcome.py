"""What one ``enrich_email`` / ``enrich_phone`` call answered, counted by the row rule (``lookups.py``).

The handler used to count ``found`` with ``deliverable(email_lookup(...))`` and ``failed`` with its own filter, and
the chat read the rest as a subtraction. Here each searched row is counted once, from the cells this call wrote,
so a batch and a row never disagree.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.lists.lookups import EmailFound, deliverable, email_lookup, phone_lookup

Contact = Literal['email', 'phone']
SearchedKind = Literal['all_found', 'partial', 'none_found', 'all_failed']


class Tally(BaseModel):
    """Each searched row counted once, by email_lookup or phone_lookup of the cells this call wrote."""

    found: int = 0  # deliverable(): what the call bills
    invalid: int = 0  # found, verdict 'invalid' (email only): shown, not billed
    not_found: int = 0
    failed: int = 0  # state 'failed', and a source could be asked: a retry can help
    no_source: int = 0  # state 'failed', and no source was on: a retry cannot help
    no_input: int = 0  # state 'not_searched' after the call: enrich_phone rows that searchable() refused

    @property
    def total(self) -> int:
        return self.found + self.invalid + self.not_found + self.failed + self.no_source + self.no_input


def kind_of(tally: Tally) -> SearchedKind:
    """The only classifier: ``searched()`` and the legacy parse both call it."""
    total = tally.total
    if total and tally.found == total:
        return 'all_found'
    if tally.found > 0:
        return 'partial'
    if total and tally.failed + tally.no_source == total:
        return 'all_failed'
    return 'none_found'


class Searched(BaseModel):
    kind: SearchedKind
    contact: Contact
    searched: int
    tally: Tally
    unmatched: int = 0  # requested ids that loaded no row
    refunded: int = 0  # set only by refunded(), at the end of the turn

    @model_validator(mode='after')
    def _consistent(self) -> Self:
        """searched == sum of the tally; kind == kind_of(tally). A tally that lies fails at construction."""
        if self.searched != self.tally.total:
            raise ValueError(f'searched {self.searched} is not the sum of the tally {self.tally.total}')
        if self.kind != kind_of(self.tally):
            raise ValueError(f'kind {self.kind} is not {kind_of(self.tally)} for the tally')
        return self


class NothingToSearch(BaseModel):
    kind: Literal['nothing_to_search']
    contact: Contact
    unmatched: int = 0


class NoRowMatch(BaseModel):
    kind: Literal['no_row_match']
    contact: Contact


class Legacy(BaseModel):
    """A call stored as ``{count, noun, ...}`` whose tally the stored counts do not determine: ``found`` is certain,
    and ``failed`` (rows a retry can help) when #530 stored it."""

    kind: Literal['legacy']
    contact: Contact
    found: int
    failed: int = 0
    refunded: int = 0  # stored by #666 and kept; no turn sets it now, so refunded() leaves a Legacy as it is


ContactOutcome = Annotated[Searched | NothingToSearch | NoRowMatch | Legacy, Field(discriminator='kind')]


def searched(
    contact: Contact, updates: Sequence[dict], no_source: Collection[str] = (), unmatched: int = 0
) -> Searched:
    """The outcome of the ``row_updates`` of a call. It reads each update's cells, not the row: an ``if_empty``
    write can be dropped later, and the outcome must describe what this call answered."""
    counts = Tally()
    for u in updates:
        cells = u['cells']
        lookup = email_lookup(cells, {}) if contact == 'email' else phone_lookup(cells, {})
        if lookup.state == 'found':
            if isinstance(lookup, EmailFound) and not deliverable(lookup):
                counts.invalid += 1
            else:
                counts.found += 1
        elif lookup.state == 'failed':
            if u['row_id'] in no_source:
                counts.no_source += 1
            else:
                counts.failed += 1
        elif lookup.state == 'not_found':
            counts.not_found += 1
        else:
            counts.no_input += 1
    return Searched(kind=kind_of(counts), contact=contact, searched=len(updates), tally=counts, unmatched=unmatched)


def refunded(outcome: ContactOutcome, rows: int) -> ContactOutcome:
    """Sets, never adds, the refunded count, so a retried settle changes nothing. A Legacy is a stored row that no
    live call produces, so it keeps the count it stored."""
    if isinstance(outcome, Searched):
        return outcome.model_copy(update={'refunded': rows})
    return outcome
