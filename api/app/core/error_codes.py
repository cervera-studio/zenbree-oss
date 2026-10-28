"""Stable error codes written to ``scrape_jobs.error_message``.

These codes are customer-visible (the FE renders the field verbatim), so they
must be stable, vendor-neutral strings.  The module intentionally avoids
importing any third-party package so it can be used from any layer without
dragging in optional deps.

Constants are plain strings rather than an enum so callers can compare with
``==`` and the values are directly JSON-serialisable without a ``.value``
call.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# ── Error code constants ─────────────────────────────────────────────────────

TIMEOUT = 'timeout'
UPSTREAM_UNAVAILABLE = 'upstream_unavailable'
RATE_LIMITED = 'rate_limited'
INVALID_URL = 'invalid_url'
AUTH_FAILED = 'auth_failed'
BLOCKED = 'blocked'
DETERMINISTIC = 'deterministic'
INTERNAL = 'internal'
MAX_RETRIES_EXCEEDED = 'max_retries_exceeded'
CONCURRENCY_CAP = 'concurrency_cap'
CANCELLED = 'cancelled'
EMPTY_EXTRACTION = 'empty_extraction'

ALL_CODES: frozenset[str] = frozenset(
    {
        TIMEOUT,
        UPSTREAM_UNAVAILABLE,
        RATE_LIMITED,
        INVALID_URL,
        AUTH_FAILED,
        BLOCKED,
        DETERMINISTIC,
        INTERNAL,
        MAX_RETRIES_EXCEEDED,
        CONCURRENCY_CAP,
        EMPTY_EXTRACTION,
        CANCELLED,
    }
)


# ── EXPORT.* namespace ───────────────────────────────────────────────────────
#
# Tool-result error_class values for the file/PDF export executors
# (``generate_file``, ``export_page_pdf``). Surface in dotted form
# (``EXPORT.RENDER_TIMEOUT``) as the top-level ``error_class`` key on the
# tool_result dict so the LLM can route retries on semantics, not on
# free-text. PR #525 fixed the immediate xlsx/PDF bugs with hardcoded
# strings; this is the systemic replacement .
#
# Semantics:
#   INVALID_INPUT       — caller passed unsupported shape (e.g. non-tabular
#                         for xlsx/csv, empty data, unknown format).
#   RENDER_FAILED       — Gotenberg returned non-2xx, R2 upload failed, or
#                         a deterministic adapter-side error not classified
#                         as timeout/unreachable.
#   RENDER_TIMEOUT      — Gotenberg timed out after retries; the LLM should
#                         try a different format (e.g. CSV instead of PDF)
#                         or fall back to markdown table in chat.
#   SOURCE_UNREACHABLE  — pre-flight HEAD/GET failed (DNS, network, 4xx/5xx
#                         on the source URL — NOT on Gotenberg itself).
#   SOURCE_SOFT_404     — pre-flight redirected to a soft-error path
#                         (/error, /404, error.html, …).
#   CONTRACT_VIOLATION  — placeholder/fabricated URL (e.g. Google Doc id
#                         shorter than 25 chars, /placeholder path segment)
#                         the LLM emitted instead of asking the user.
class EXPORT:  # namespace, not an enum
    """EXPORT.* error_class values (dotted form). See module docstring."""

    INVALID_INPUT = 'EXPORT.INVALID_INPUT'
    RENDER_FAILED = 'EXPORT.RENDER_FAILED'
    RENDER_TIMEOUT = 'EXPORT.RENDER_TIMEOUT'
    SOURCE_UNREACHABLE = 'EXPORT.SOURCE_UNREACHABLE'
    SOURCE_SOFT_404 = 'EXPORT.SOURCE_SOFT_404'
    CONTRACT_VIOLATION = 'EXPORT.CONTRACT_VIOLATION'


EXPORT_CODES: frozenset[str] = frozenset(
    {
        EXPORT.INVALID_INPUT,
        EXPORT.RENDER_FAILED,
        EXPORT.RENDER_TIMEOUT,
        EXPORT.SOURCE_UNREACHABLE,
        EXPORT.SOURCE_SOFT_404,
        EXPORT.CONTRACT_VIOLATION,
    }
)

# Human-readable descriptions — one line per code, no trailing period. Used
# by observability surfaces (BetterStack charts, error inventories) and by
# the unit test that pins both the set of codes and the existence of a
# description for each. Keep these short and customer-friendly; the LLM
# does not see them directly (it sees ``error_class`` + ``error`` detail),
# but operators do.
EXPORT_DESCRIPTIONS: dict[str, str] = {
    EXPORT.INVALID_INPUT: (
        'Caller passed unsupported input shape (e.g. non-tabular data for xlsx/csv, empty data, or an unknown format)'
    ),
    EXPORT.RENDER_FAILED: (
        'Renderer returned an unrecoverable error (Gotenberg non-2xx, '
        'openpyxl raise, R2 upload failure, sidecar unavailable)'
    ),
    EXPORT.RENDER_TIMEOUT: (
        'Renderer timed out after the bounded retry budget; the LLM should '
        'try a different format or fall back to inline content'
    ),
    EXPORT.SOURCE_UNREACHABLE: (
        'Source URL could not be reached (DNS failure, connection refused, '
        'HTTP error on preflight, or SSRF policy block)'
    ),
    EXPORT.SOURCE_SOFT_404: (
        'Source URL responded but the body is a soft-error page '
        '(Google Doc placeholder, /error or /404 redirect, error-page HTML)'
    ),
    EXPORT.CONTRACT_VIOLATION: (
        'Tool result violated the declared output schema (G1 post-validator '
        'rejection — e.g. ELTI-style single-cell table claiming success)'
    ),
}


# ── TOOL.* namespace ─────────────────────────────────────────────────────────
#
# Tool-result error_class values for refusals the MODEL can fix by itself.
#
# INVALID_ARGUMENTS marks a refusal produced by argument validation: a missing
# required identifier, an empty query, a parameter the platform does not take.
# WRONG_LANGUAGE marks the same kind of refusal decided on the ARGUMENT TEXT:
# an artifact whose columns, note or plan steps are written in a language this
# session does not speak, refused before it is born .
# Such a call never reached the provider, so it says nothing about whether the
# channel works, and the per-session failure breaker must not count it
# (API#1626 — five malformed social_ad_details calls opened the breaker,
# which then blocked the five the model had just rebuilt correctly).
class TOOL:  # namespace, not an enum
    """TOOL.* error_class values. See the note above."""

    INVALID_ARGUMENTS = 'TOOL.INVALID_ARGUMENTS'
    WRONG_LANGUAGE = 'TOOL.WRONG_LANGUAGE'
    # A key the tool's schema does not declare, refused before dispatch
    # (#1931/#2303). Same family as INVALID_ARGUMENTS: decided entirely on
    # the arguments, no provider dialled, and the message names both the
    # offending keys and the declared ones — the caller can fix it in the
    # same turn, so the failure breaker must not count it.
    UNDECLARED_ARGUMENT = 'TOOL.UNDECLARED_ARGUMENT'


# Tool-result error_class values for the by-id recall path (read_tool_result).
#
# The three say different things about the CHANNEL, which is why they are three
# and not one. UNKNOWN_ID and TOOL_MISMATCH are decided against what the store
# answered, so they are the caller's mistake and are exempt from the failure
# breaker; NOT_FOUND means the store holds the result and could not serve it,
# which is a channel outage and must keep counting (API#1671 — four
# hallucinated ids opened the breaker and left the session no way to re-read
# data it had already paid for, so it bought it again).
class TOOL_RESULT:  # namespace, not an enum
    """TOOL_RESULT.* error_class values. See the note above."""

    NOT_FOUND = 'TOOL_RESULT.NOT_FOUND'
    UNKNOWN_ID = 'TOOL_RESULT.UNKNOWN_ID'
    TOOL_MISMATCH = 'TOOL_RESULT.TOOL_MISMATCH'


# Use-case error codes that mean the same thing as TOOL.INVALID_ARGUMENTS.
#
# A handler that funnels every use-case error through one ``tool_result.err``
# cannot tag that return: the same line carries a dead gateway or an
# unreachable store. It already passes ``code=result.error.code`` though, so
# the breaker reads the code and the decision lives here, once, instead of in
# a private set per handler that goes stale the next time a sibling is added.
#
# Membership rule, in two parts, both required. The outcome must say nothing
# about whether the channel works, and its message must tell the caller what to
# send instead. Reading our own store first is allowed (an artifact is loaded
# before its rows can be checked for width); what is not allowed is a code with
# even ONE producer that reached a provider, because the breaker then reads a
# real outage as a caller mistake and never opens.
#
# What is deliberately NOT here, because each one travels the same handler
# return as a code above: LEADS.NO_OWNER and CONTACTS.NOT_CONFIGURED (a broken
# request or an unconfigured environment, which no input fixes),
# DASHBOARD.EMPTY_KG (the graph has nothing in it yet), OUTREACH.INVALID_OUTPUT
# (the model answered in a shape we could not read), SCHEDULE.CAP_REACHED and
# SCHEDULE.DB_INSERT_FAILED (the database was reached), and the CAREERS lookup
# codes NAME_UNRESOLVED / NOT_FOUND / SCRAPE_FAILED / NO_RESOLVER.
ARGUMENT_REFUSAL_CODES: frozenset[str] = frozenset(
    {
        # Artifacts: an id, a section or a revert target the caller got wrong.
        'ARTIFACT.INVALID_INPUT',
        'ARTIFACT.INVALID_REVERT',
        'SPREADSHEET.INVALID_INPUT',
        # Career discovery and scraping: a URL or a company identifier we can
        # reject on shape. NAME_UNRESOLVED and NOT_FOUND need the host probed.
        'CAREERS.INVALID_DOMAIN',
        'CAREERS.INVALID_INPUT',
        'CAREERS.INVALID_URL',
        # Marketplace product search: empty keywords, a pasted sentence where a
        # product name belongs, an Amazon storefront the provider does not run.
        # Every producer is a local check in
        # ``app/commerce/use_cases/search_products.py`` that runs BEFORE the
        # provider is dialled, and each message names what to send instead.
        'COMMERCE.INVALID_ARGUMENTS',
        # ...and the rollout gate being off. Nothing was dialled and nothing was
        # billed, so the refusal proves nothing about the channel's health and
        # must not push the tool toward the failure breaker. It is not the
        # caller's mistake either — no argument opens a gate — but "does not
        # count against the channel" is what this set decides, and that is the
        # right answer for it. See app/chat/tools/commerce_tools_flag.py.
        'COMMERCE.NOT_ENABLED',
        # Push destinations: nothing to write, or more rows or columns than the
        # sheet takes. Both are counted off the payload the caller handed us.
        'DESTINATIONS.EMPTY',
        'DESTINATIONS.TOO_LARGE',
        # Lead pipeline: a missing lead id. NO_OWNER is the request context.
        'LEADS.INVALID_INPUT',
        # Italian company register: a search wide enough to need confirmation asks
        # for it before spending. OPENAPI_IMPRESE.INVALID_INPUT is NOT here even
        # though two of its producers are local checks: openapi.com's own 422 maps
        # to it, and so does the spend gate, which fires after a paid count(). One
        # code cannot answer for all three, and the tool's own argument refusals
        # are tagged at the handler instead.
        'OPENAPI_IMPRESE.CONFIRM_REQUIRED',
        # People search: title and location counts, page bounds.
        'PEOPLE.INVALID_INPUT',
        # Shared validation vocabulary of the people use cases.
        'VALIDATION.INVALID_SORT',
        'VALIDATION.MISSING_DOMAIN',
        'VALIDATION.MISSING_FILTER',
        # Outreach: a required field the caller omitted, checked so we never pay
        # for the model round trip.
        'OUTREACH.MISSING_INPUT',
        # Scheduling: name, cron and tool name are all validated in process.
        # CAP_REACHED comes through the same return after counting rows, and
        # DB_INSERT_FAILED and NOT_FOUND are the store, so all three keep counting.
        'SCHEDULE.CRON_EMPTY',
        'SCHEDULE.CRON_INVALID',
        'SCHEDULE.NAME_EMPTY',
        'SCHEDULE.NAME_TOO_LONG',
        'SCHEDULE.TOOL_REQUIRED',
        'SCHEDULE.TOOL_UNKNOWN',
        # SEO lookups: an empty query, an oversized keyword batch, an unknown
        # backlink scope. All decided in process before the provider is dialled,
        # so none of them says anything about whether Treg is reachable. The
        # family's other codes are NOT here and must keep counting:
        # SEO.NOT_CONFIGURED is a missing token, SEO.OUT_OF_BALANCE is a drained
        # prepaid balance, and SEO.TRANSPORT_ERROR reached the proxy — the last
        # two are exactly the outages the breaker exists to notice, and both are
        # shared with enrich_email through the same Treg account. SEO.NO_RESULTS
        # is not here either, and it is the tempting one: a provider that answered
        # "no data" is not a broken channel, and its message does tell the caller
        # something. It fails the rule all the same, because the rule is about the
        # producer and not about the sentiment: that code is minted only after a
        # provider was reached (#2239), exactly like CONTACTS.TREG_PROVIDER_NO_MATCH,
        # which is outside this set for the same reason. Being outside it is not the
        # same as counting as a failure, though: both are in
        # PROVIDER_ANSWERED_MISS_CODES below, which the breaker reads as a delivery.
        # The two sets are disjoint and answer different questions — this one "did
        # the caller send something we can fix", that one "did the provider speak".
        'SEO.INVALID_INPUT',
        # ...and the rollout gate being off, for the same reason
        # COMMERCE.NOT_ENABLED is here: nothing was dialled, so the refusal is
        # no evidence about the channel. Without the exemption a family that is
        # OFF in this environment would trip its own breaker.
        'SEO.NOT_ENABLED',
        # Travel: everything else in that family is a lookup that blew up against
        # Ryanair, Wizz, Booking or Airbnb.
        'TRAVEL.BAD_CARRIER',
        'TRAVEL.BAD_DATE',
        'TRAVEL.BAD_DEST',
        'TRAVEL.BAD_LOCATION',
        'TRAVEL.BAD_ORIGIN',
        'TRAVEL.BAD_SOURCE',
        'TRAVEL.WIZZ_NEEDS_DEST',
        'TRAVEL.WIZZ_NO_RETURN',
    }
)


# Use-case error codes that mean "a provider answered, and it has no record".
#
# The failure breaker counts a channel failure: the provider was reached and
# could not deliver. An answered miss is the opposite of that. It is the
# channel working — the lookup ran, the provider replied, and the reply is
# "nobody has this". Making it count is how a session that asks about five
# obscure domains in a row loses the tool for two hours over five healthy
# lookups .
#
# The three Treg contexts disagreed on this, each in its own file: commerce
# absorbed the miss into an empty Ok and so cleared the breaker, while SEO and
# people surfaced it as an Err and so pushed the tool toward opening. Same
# provider, same account, same 404 relayed through the same proxy, three
# answers. The decision lives here, once, for the same reason the set above
# does: a private set per handler goes stale the next time a sibling context
# is added (#1626).
#
# Membership rule, the inverse of the one above: EVERY producer of the code
# must run after a provider answered. One producer that can mint the code
# before the call is made (or after it failed to land) disqualifies it, because
# the breaker would then read a dead channel as a delivery and never open.
# Sentiment is not the test — "we found nothing" phrasing earns a code nothing
# if a local check can also produce it.
#
# What is deliberately NOT here:
#   * ENRICHMENT.INVALID_HINTS — decided off the hints before anything is
#     dialled. Nobody answered, because nobody was asked.
#   * SEO.OUT_OF_BALANCE, SEO.TRANSPORT_ERROR, CONTACTS.TREG_OUT_OF_BALANCE —
#     the proxy was reached and no provider was, which is the outage the
#     breaker exists to notice.
#   * CONTACTS.REVEAL_FAILED — a reveal that raised, or a batch holding one.
#   * CONTACTS.MATCH_INSUFFICIENT_INPUT — decided off the hints, before any
#     chain is built.
PROVIDER_ANSWERED_MISS_CODES: frozenset[str] = frozenset(
    {
        # Crunchbase, via the same Treg proxy: the search resolved no company, or
        # the organization it did read carried nothing usable. Both producers in
        # ``app/knowledge/adapters/crunchbase_company_enrichment.py`` sit on an Ok
        # from the proxy, so Crunchbase answered in both.
        'ENRICHMENT.CRUNCHBASE_NO_MATCH',
        # Marketplace search. Minted on a RELAYED 404 in
        # ``app/commerce/treg_client.py`` and turned back into an empty Ok by
        # ``app/commerce/adapters/treg_product_search.py``, so today it never
        # reaches a tool result at all. Listed anyway: this set is the family
        # enumerated once, and commerce's local answer to the question is the one
        # the other two contexts are being brought in line with.
        'COMMERCE.TREG_PROVIDER_NO_MATCH',
        # Contact reveal. TREG_PROVIDER_NO_MATCH is the relayed 404 as
        # ``app/people/treg_client.py`` mints it; TREG_NO_MATCH is what the
        # enrichment adapter makes of a chain that ran to the end, and each of its
        # producers sits after a provider replied — a relayed 404, or a 200 with no
        # usable contact channel. A chain where some providers were unreachable and
        # one still answered "no record" surfaces this code with the unreachable
        # ones named in the envelope: the tool delivered its verdict, so the
        # breaker treats it as a delivery. The adapter's ``exhausted`` fallback
        # (``treg_contact_enrichment.py``, the third arm of ``balance_err or
        # last_err or err(...)``) is the one spelling of it that would satisfy
        # nobody, and it is dead code: ``_plan`` refuses an empty attempt list
        # before the loop, and every arm of the loop either returns or sets one of
        # the two errors. Give that arm a way to fire and it hands the breaker a
        # delivery from a chain that dialled no one.
        'CONTACTS.TREG_NO_MATCH',
        'CONTACTS.TREG_PROVIDER_NO_MATCH',
        # Identity guard. The proxy relayed a 200 with usable contact channels,
        # but no name on an anchored row whose lookup was not pinned by an email
        # or a LinkedIn URL — the adapter refused to land it. Pinned lookups are
        # exempt: the sub-provider matched on a 1:1 identifier.
        # Single producer: ``treg_contact_enrichment.reveal``, always on an Ok
        # from the proxy, so a provider answered.
        'CONTACTS.TREG_IDENTITY_UNCONFIRMED',
        # Row-identity guard (#2079). A provider answered a reveal with a usable
        # contact channel and a LinkedIn profile that names a different human than
        # the one the anchored row is known by, so the handler refused to write it.
        # Single producer: ``_handle_enrich_email``, strictly after
        # ``has_contact_channel`` is true, so a provider always answered.
        'CONTACTS.ROW_IDENTITY_CONTRADICTED',
        # Apollo's own two verdicts on a reveal, and the ones the chat surface
        # actually shows most often: NO_MATCH decodes a 200 that carried no
        # person, NO_CONTACT_REVEALED is a person Apollo did match and had no
        # email or LinkedIn for. Both are read off a payload the provider sent.
        # NO_CONTACT_REVEALED is also the bulk reveal's verdict on a batch, and
        # only when every row in it was answered — a batch holding a reveal that
        # blew up says CONTACTS.REVEAL_FAILED instead, so this code keeps the
        # single meaning the rule needs (app/chat/tools/handlers/people.py).
        'CONTACTS.NO_MATCH',
        'CONTACTS.NO_CONTACT_REVEALED',
        # A company nobody has a profile for, which is the third context the
        # breaker was sealing over. Six producers. Five read a reply that arrived:
        #   * ``_decode_organization_enrich`` in ``app/people/apollo_decode.py``,
        #     on a 404 and on the 200-with-organization-null some Apollo plan
        #     tiers send instead;
        #   * ``_decode_job_postings`` in the same module, on a 404 for an
        #     organization id;
        #   * ``EnrichCompany.resolve``
        #     (``app/people/use_cases/enrich_company.py``), twice: no candidate in
        #     the answered search shares a word with the name, or the one that
        #     ranked first carries no website. Both sit on an Ok from
        #     ``search_organizations``, so Apollo answered before either is
        #     reached, and neither is the local check an earlier draft of this
        #     preamble claimed;
        #   * ``_handle_company_job_postings`` in
        #     ``app/chat/tools/handlers/companies_jobs.py``, on ``_pick_match``
        #     finding nothing in an answered search;
        #   * the two restatements in
        #     ``app/chat/tools/handlers/companies_enrich.py``, which only re-emit
        #     one of the above once the chain is terminal.
        # The handler's ``_NO_MATCH_CODES`` is what keeps that last one honest: it
        # holds only codes a source that RAN produced, which is why
        # ENRICHMENT.INVALID_HINTS was taken out of it .
        #
        # The sixth reads nothing: ``_cached_miss_envelope`` in the same handler
        # replays a miss this session already bought (#2124), and no provider is
        # dialled for it. It is exempt by ORDERING and not by the rule above, which
        # is worth stating rather than leaving to be rediscovered: the executor
        # recognises a session replay and returns from
        # ``_replay_from_session_memory`` BEFORE any breaker is consulted, so that
        # envelope never reaches ``record_result`` in either direction. Move the
        # replay below the breaker and this code stops satisfying the rule, so the
        # move has to bring its own answer for that.
        #
        # The batch tool reaches the same envelope by a second path and is exempt
        # for a second reason, which is worth writing down rather than rediscovering
        # under a debugger: ``enrich_companies`` is not in the executor's
        # ``_SESSION_MEMORY_TOOLS``, so it does dispatch and does reach the breaker,
        # but it asks the memory per ROW and a replayed miss never becomes the
        # envelope's own code: no return of that handler carries it, and the one
        # verdict that counts a batch as failed, ENTITY.INFRA_ERROR, is reached on
        # a tally the replayed miss is excluded from twice over, by the handler's
        # ``_NO_MATCH_CODES`` and by arriving marked ``cached``. Stated as what the
        # code is NOT rather than as a list of what the handler returns, because
        # this entry has already shipped two lists that read as complete and were
        # not, and only the negative is what the rule above needs.
        'CONTACTS.ORG_NOT_FOUND',
        # SEO lookups. Minted only on a relayed 404 in ``app/seo/treg_client.py``
        # (#2239): Moz was asked about a target it has never crawled. The rest of
        # the SEO family stays out, per the note above.
        'SEO.NO_RESULTS',
    }
)


def export_description(code: str) -> str:
    """Return the human-readable description for an EXPORT.* code.

    Raises ``KeyError`` for codes not in :data:`EXPORT_CODES` — callers
    should verify membership first, but the strictness keeps the
    description map honest (a new EXPORT.* constant without a description
    fails the unit test loudly instead of silently returning empty).
    """
    return EXPORT_DESCRIPTIONS[code]


# ── Classifier ───────────────────────────────────────────────────────────────


def classify_exception(exc: BaseException) -> str:
    """Map any exception to a stable, vendor-neutral error code.

    Uses class-name string matching and light ``isinstance`` checks for stdlib
    types only — no vendor imports.  Never raises; unknown shapes fall back to
    ``INTERNAL``.

    Args:
        exc: The exception to classify.

    Returns:
        One of the module-level code constants in ``ALL_CODES``.
    """
    try:
        return _classify(exc)
    except Exception:
        logger.exception('error_codes.classify_exception.unexpected', exc_info=True)
        return INTERNAL


def _classify(exc: BaseException) -> str:
    cls_name = type(exc).__name__
    message = str(exc)

    # ── SSRF policy block ────────────────────────────────────────────────────
    # A blocked destination is a deterministic policy refusal, never a
    # transport error and never retryable. Classify by class name (no
    # vendor import) BEFORE the status-string heuristics below, which would
    # otherwise mis-read a blocked port like ``:403`` / ``:429`` in the
    # message ("SSRF blocked at proxy hop: 10.0.0.5:403") as an auth /
    # rate-limit failure and advise the model to retry (#2362).
    if cls_name == 'SSRFBlockedError':
        return BLOCKED

    # ── Timeout ──────────────────────────────────────────────────────────────
    if isinstance(exc, asyncio.TimeoutError):
        return TIMEOUT
    if cls_name.endswith('TimeoutError') or 'Timeout' in cls_name:
        return TIMEOUT
    # subprocess.TimeoutExpired
    if cls_name == 'TimeoutExpired':
        return TIMEOUT

    # ── Cancelled ────────────────────────────────────────────────────────────
    if isinstance(exc, asyncio.CancelledError):
        return CANCELLED
    if cls_name in ('SoftTimeLimitExceeded', 'TimeLimitExceeded'):
        return CANCELLED

    # ── Connection / upstream unavailable ────────────────────────────────────
    if isinstance(exc, ConnectionError):
        return UPSTREAM_UNAVAILABLE
    if 'ConnectionError' in cls_name or 'Connect' in cls_name:
        return UPSTREAM_UNAVAILABLE
    if cls_name in ('NetworkError', 'ServiceUnavailableError'):
        return UPSTREAM_UNAVAILABLE

    # ── HTTP-status-aware heuristics (parse the message string) ─────────────
    # Check for 401/403 (auth) and 429 (rate limit) before generic 4xx.
    if _message_has_status(message, {401, 403}):
        return AUTH_FAILED
    if _message_has_status(message, {429}):
        return RATE_LIMITED
    if _message_has_status(message, set(range(400, 500))):
        return INVALID_URL
    if _message_has_status(message, set(range(500, 600))):
        return UPSTREAM_UNAVAILABLE

    # ── Deterministic / misconfiguration ─────────────────────────────────────
    if isinstance(exc, ImportError):
        return DETERMINISTIC
    if isinstance(exc, (ValueError, TypeError)):
        return DETERMINISTIC

    # ── Default ──────────────────────────────────────────────────────────────
    return INTERNAL


def _message_has_status(message: str, statuses: set[int]) -> bool:
    """Return True if any of *statuses* appears as a substring in *message*."""
    return any(str(s) in message for s in statuses)
