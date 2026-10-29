"""Apollo → Provenance mapping (P0-2 adopter, #842).

Apollo's people endpoints surface the first **meaningful confidence
signal** in the Provenance contract (#830, #831). The FE chip
reads ``confidence`` ∈ [0.0, 1.0] and renders a
green/yellow/red colour band — recruiters and sales personas use it
to decide whether to act on a contact before spending outreach time.

Mapping (per Apollo's published ``email_status`` values + their docs
on probable matches, verified against ``app/people/apollo_models.py``
docstrings):

- ``verified``   → 1.0  (green chip — safe for cold outbound)
- ``probable``   → 0.7  (yellow chip — probable match, treat with care)
- ``guessed``    → 0.4  (red chip — pattern-guessed, will bounce)
- ``unverified`` / missing / None → ``None``  (no chip; explicitly
  surfaced by Apollo as "do not use for cold outbound")

We intentionally do NOT map ``unverified`` to a number. Apollo
distinguishes "we tried and could not verify" from "we have no
signal at all" — both should suppress the chip rather than show a
red one the user might misread as a meaningful low score. The FE
treats ``confidence == None`` as "no chip rendered" by contract.

Single source of truth: this module. Both ``find_decision_maker`` and
``enrich_email`` chat-tool handlers import from here so the mapping
never drifts.
"""

from __future__ import annotations

from app.core.provenance import Provenance, build_provenance

# Categorical mapping for Apollo's ``email_status`` enum.
# ``unverified`` is intentionally absent — see module docstring.
_APOLLO_CONFIDENCE_BY_STATUS: dict[str, float] = {
    'verified': 1.0,
    'probable': 0.7,
    'guessed': 0.4,
}

# Canonical extraction_method tag for a REVEALED contact — the record the
# provider maintains and the user paid to see.
APOLLO_EXTRACTION_METHOD = 'apollo_verified'

# The free preview row is NOT that record and must not wear its badge (#2020).
# It comes off the provider's search index, a different artefact with a
# different age: #1911 measured index rows still carrying a title the reveal
# had replaced months earlier. Sharing one tag made the row the user reads
# BEFORE spending 35 credits claim exactly the grade of verification the spend
# is supposed to buy — the asymmetry was invisible on ``title``,
# ``organization`` and both channel flags at once. One label separates all
# four, because all four come from the same index hit.
APOLLO_SEARCH_INDEX_EXTRACTION_METHOD = 'apollo_search_index'

# Tag for a reveal that came back through the Treg aggregator instead of
# Apollo. One generic tag, not one per sub-provider: Treg proxies the
# upstream response verbatim, so the payload never says whether PDL or
# Hunter answered (see ``ContactSource`` for the full
# reasoning).
#
# This used to end "the FE renders a per-method label from this string and an
# unlisted method degrades to no label rather than breaking — so shipping this
# before the matching web app change is cosmetic, not a bug". The label half
# is still true; the conclusion is not, and #2020 is what surfaced why. The FE
# decides whether a row may show a "View source" link from the method's FAMILY
# (``isDirectorySourced`` → ``provenanceSourceHref``, packages/types), and a
# method the registry has never heard of has no family — so the
# homepage-anchor suppression web app#607 shipped is OFF for exactly the
# tags nobody registered. A Treg reveal whose ``source_url`` fell back to the
# employer domain therefore already offers a link to a page documenting
# neither the person nor the address. Tracked separately; the fix is the
# web app registry entry, not a change here.
TREG_EXTRACTION_METHOD = 'treg_aggregated'

_EXTRACTION_METHOD_BY_SOURCE: dict[str, str] = {
    'apollo': APOLLO_EXTRACTION_METHOD,
    'treg': TREG_EXTRACTION_METHOD,
    # Same directory-verified tag as Apollo, on purpose: a new method string
    # has no family in web app's registry until it is registered there, and
    # a family-less tag switches the homepage-link suppression off (see the
    # Treg note above). The provider stays readable on ``ContactSource``.
    'aiark': APOLLO_EXTRACTION_METHOD,
}


def apollo_confidence(verification_status: str | None) -> float | None:
    """Map Apollo's verification status to a 0.0-1.0 confidence score.

    Returns ``None`` for ``unverified``, missing, ``None``, or any
    status not in the known set — the FE chip then suppresses
    rendering rather than guess at a colour band.

    Case-insensitive: Apollo has historically been inconsistent
    between ``"Verified"`` and ``"verified"`` across endpoints, so we
    normalise. Non-string inputs (defensive: a malformed adapter
    payload) also return ``None``.
    """
    if not isinstance(verification_status, str):
        return None
    return _APOLLO_CONFIDENCE_BY_STATUS.get(verification_status.strip().lower())


def apollo_provenance(
    *,
    source_url: str | None,
    confidence: float | None,
) -> Provenance:
    """Build a Provenance VO for a REVEALED Apollo row.

    Thin wrapper over :func:`app.core.provenance.build_provenance`
    that pins ``extraction_method='apollo_verified'`` so callers can
    not accidentally drift the tag — the FE renders a per-method
    label from this string and any mistype silently breaks the chip.

    Reveal only. A free search-index row goes through
    :func:`apollo_preview_provenance`, which claims neither the
    verification grade nor a collection time (#2020); a ratchet test
    holds new call sites to that split.
    """
    return build_provenance(
        source_url=source_url,
        extraction_method=APOLLO_EXTRACTION_METHOD,
        confidence=confidence,
    )


def apollo_preview_provenance(*, source_url: str | None) -> Provenance:
    """Build a Provenance VO for a FREE preview row off the search index.

    Two things this deliberately does not say.

    It does not say ``apollo_verified``: see
    :data:`APOLLO_SEARCH_INDEX_EXTRACTION_METHOD`.

    And it does not say WHEN. It builds the model directly instead of going
    through :func:`~app.core.provenance.build_provenance`, whose
    ``scraped_at`` default is ``now()`` — the one value that is certainly
    wrong here. Our call is the instant we asked the index, not the instant
    the index collected the row, and the FE reads the field as the latter and
    prints "scraped just now". Leaving it unset is not a gap to be filled in
    later by whoever adds an age: it is the honest answer, and the surfaces
    already treat absence as "no freshness to show" rather than falling back
    to the moment the tab opened.

    ``confidence`` is unset for the same class of reason it always was — no
    email is revealed at this stage, so there is no ``email_status`` to grade.

    No preview counterpart to :func:`contact_provenance` exists because the
    aggregator only ever answers a reveal; the index search is Apollo's alone.
    """
    return Provenance(
        source_url=source_url,
        extraction_method=APOLLO_SEARCH_INDEX_EXTRACTION_METHOD,
    )


def contact_provenance(
    *,
    source: str,
    source_url: str | None,
    confidence: float | None,
) -> Provenance:
    """Build a Provenance VO for a revealed contact, whoever revealed it.

    ``source`` is a :class:`app.people.ports.contact_enrichment.ContactSource`
    value (a StrEnum, so a plain ``'apollo'`` / ``'treg'`` works too — taken
    as a string here to keep this module a leaf that the port does not have
    to import back). An unknown source falls back to the Apollo tag, which
    is the status quo for every row that existed before Treg.
    """
    return build_provenance(
        source_url=source_url,
        extraction_method=_EXTRACTION_METHOD_BY_SOURCE.get(
            str(source),
            APOLLO_EXTRACTION_METHOD,
        ),
        confidence=confidence,
    )
