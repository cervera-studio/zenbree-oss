"""Google Maps / Places local-business search via Serper's ``/places``.

Why Serper and not the official Google Places API (#1076): the key is
already provisioned end-to-end (``settings.serper_api_key`` → Terraform →
Key Vault → container-apps, prod + staging), while a Places API key does
not exist in config and the GCP project sits outside our org. Same Google
Maps corpus, zero new credentials, zero new Terraform.

Output is the local-lead row shape the chat tool speaks — name / address /
website / phone / rating / review_count — so results chain straight into
``fetch_page`` (technographics) and the enrichment tools.

Paging contract, measured 2026-08-27 against the provisioned key with
``q="ristorante"``, ``location="Milano, Italia"``: a page holds exactly 10
rows, ``page`` 1..7 all answer 200 with disjoint results (70 distinct cids,
no overlap), and ``num`` is not the knob to widen a page: ``num: 20`` and
``num: 40`` both return zero results. A page that comes back with rows is a
billed call: the probe sent ten POSTs and the balance went 49938 -> 49930,
eight of them charged, which lines up with the two zero-result ``num`` probes
costing nothing. ``GET /account`` is free. So the walk stops at the first
short page rather than buying pages the listing does not have.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.core.config import settings
from app.core.error_codes import TOOL

logger = structlog.get_logger()

_URL = 'https://google.serper.dev/places'
_TIMEOUT = 30
_DEFAULT_LIMIT = 20
_PAGE_SIZE = 10  # Fixed by the vendor, see the paging contract in the module docstring.
_MAX_LIMIT = 60  # 6 billed pages is the ceiling for one call.


def _unavailable(query: str) -> dict[str, Any]:
    """Vendor-free, code-free error envelope.

    The model echoes tool errors into its prose and the FE renders the raw
    envelope, so a leaked ``"serper 402"`` becomes user-visible — the exact
    failure mode #1015 shipped to staging. Say only what the user can act on.

    It also names the road, because a refusal that does not is read as "the
    product cannot do this": on staging (#1666) the model met this envelope,
    fell silently back to web search and delivered 12 workshops of which one
    carried a rating, without ever telling the user the Maps fields were
    missing. Nothing here identifies the vendor or the status code.
    """
    return {
        'success': False,
        'error': (
            'local business search is temporarily unavailable; web_search can still'
            ' find names and addresses, but ratings and review counts are not'
            ' available for this request and must be reported as missing'
        ),
        'query': query,
        'places': [],
    }


def is_credit_exhaustion(status: int, detail: str) -> bool:
    """Whether an error response is the empty-account wall.

    Measured 2026-08-04 on the provisioned key: ``/places`` answers
    ``400 {"message":"Not enough credits"}``, not the 402 the code would
    suggest, so the status alone cannot classify it. 402 is kept as a second
    marker in case the vendor ever moves to the canonical code.

    Public because a second Serper-backed road exists (``google_reviews``,
    #1613) and two roads that disagree about what a dry account looks like
    would leave the depleted verdict flapping.
    """
    return status == 402 or 'not enough credits' in detail.lower()


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ''


def _number(value: Any) -> float | int | None:
    """Serper sends rating/ratingCount as numbers, but a string slips through
    on some rows. Never coerce a bool (``True`` is an int in Python)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return value
    try:
        return float(value)
    except TypeError, ValueError:
        return None


def normalize_place(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Project one Serper ``places[]`` entry onto our local-lead row.

    Returns ``None`` for an entry with no business name — an unnamed row is
    not a lead, and keeping it would let the model invent one.
    """
    name = _clean(raw.get('title'))
    if not name:
        return None
    cid = _clean(raw.get('cid'))
    row: dict[str, Any] = {
        'name': name,
        'address': _clean(raw.get('address')),
        'website': _clean(raw.get('website')),
        'phone': _clean(raw.get('phoneNumber')),
        'rating': _number(raw.get('rating')),
        'review_count': _number(raw.get('ratingCount')),
        'category': _clean(raw.get('category')),
        'latitude': _number(raw.get('latitude')),
        'longitude': _number(raw.get('longitude')),
        # ``placeId`` is documented but not sent: 0 of the 16 rows returned
        # across 7 queries on 2026-08-07 carried one, while every row carried
        # a ``cid``. The key stays, empty, for the day the vendor fills it in,
        # and the cid is exposed beside it because it is the identifier that
        # actually reaches a listing (it is what ``mine_reviews`` pages
        # reviews with). A cid is not a Place ID and must never be passed off
        # as one.
        'place_id': _clean(raw.get('placeId')),
        'cid': cid,
        'maps_url': f'https://www.google.com/maps?cid={cid}' if cid else '',
    }
    hours = raw.get('openingHours')
    if isinstance(hours, dict) and hours:
        row['opening_hours'] = hours
    return row


def _identity(row: dict[str, Any]) -> str:
    """Key a row across pages: cid first, it is the identifier that reaches a listing."""
    return row['cid'] or row['place_id'] or f'{row["name"].lower()}|{row["address"].lower()}'


def _fetch_page(payload: dict[str, Any], page: int) -> list[dict[str, Any]] | None:
    """One billed ``/places`` call. Returns its raw entries, or ``None`` if it failed."""
    body_payload = payload if page == 1 else {**payload, 'page': page}
    try:
        resp = httpx.post(
            _URL,
            json=body_payload,
            headers={'X-API-KEY': settings.serper_api_key, 'Content-Type': 'application/json'},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
    except httpx.HTTPStatusError as e:
        # 400 "Not enough credits" and 403 bad key both land here, and telling
        # them apart is the point (#1666): one is an empty account somebody has
        # to top up, the other is a wrong secret. Never surfaced to the user.
        detail = e.response.text[:200]
        if is_credit_exhaustion(e.response.status_code, detail):
            logger.error('serper.depleted', status=e.response.status_code)
        else:
            # Status only: a vendor body is never logged (it could echo the request).
            logger.warning('serper_places.http_error', status=e.response.status_code, page=page)
        return None
    except Exception as e:
        logger.warning('serper_places.failed', error_type=type(e).__name__, page=page)
        return None

    raw_places = body.get('places') if isinstance(body, dict) else None
    return [entry for entry in raw_places or [] if isinstance(entry, dict)]


def search_places(
    query: str,
    *,
    location: str | None = None,
    gl: str | None = None,
    hl: str | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Search Google Maps for local businesses matching ``query``.

    ``location`` is Serper's free-text geo ("Milan, Lombardy, Italy");
    ``gl`` the 2-letter country and ``hl`` the UI language.

    Walks ``page`` until ``limit`` rows are in hand or a page comes back short,
    because one page holds 10 rows and asking for 40 used to return 10 while
    reporting ``truncated: false``.
    """
    cleaned = query.strip()
    if not cleaned:
        return {
            'success': False,
            'error': 'query is required',
            'query': '',
            'places': [],
            'error_class': TOOL.INVALID_ARGUMENTS,
        }
    if not settings.serper_api_key:
        logger.warning('serper_places.unconfigured')
        return _unavailable(cleaned)

    limit = max(1, min(int(limit), _MAX_LIMIT))
    payload: dict[str, Any] = {'q': cleaned}
    for key, value in (('location', location), ('gl', gl), ('hl', hl)):
        if value:
            payload[key] = str(value).strip()

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    pages_fetched = 0
    exhausted = False
    for page in range(1, -(-limit // _PAGE_SIZE) + 1):
        entries = _fetch_page(payload, page)
        if entries is None:
            if not rows:
                return _unavailable(cleaned)
            break
        pages_fetched += 1
        for row in (normalize_place(entry) for entry in entries):
            if row is None:
                continue
            identity = _identity(row)
            if identity in seen:
                continue
            seen.add(identity)
            rows.append(row)
        if len(entries) < _PAGE_SIZE:
            # A short page is the end of the listing: buying the next one pays
            # for rows that do not exist.
            exhausted = True
            break
        if len(rows) >= limit:
            break

    truncated = not exhausted
    if truncated:
        logger.info('serper_places.truncated', found=len(rows), limit=limit)
    logger.info(
        'serper_places.ok',
        query=cleaned[:80],
        count=min(len(rows), limit),
        pages=pages_fetched,
    )
    return {
        'success': True,
        'query': cleaned,
        'location': location or '',
        'places': rows[:limit],
        'count': min(len(rows), limit),
        'truncated': truncated,
        'pages': pages_fetched,  # billed calls, for vendor_calls
    }
