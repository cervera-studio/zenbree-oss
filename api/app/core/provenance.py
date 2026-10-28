"""Provenance value object — origin + freshness + extraction method + confidence.

Every tool result that surfaces data to the user can carry a
:class:`Provenance` block. The frontend's confidence chip and
freshness badge  read from this block; the
synthesis_grounding check (#522 G9) can also consult it for
extraction-method-aware citation policies.

Design goals:

- **Optional throughout.** A tool that doesn't know its own
  provenance (yet) omits the block — null is a valid value at every
  layer (schema, serializer, FE). The chip just doesn't render.
- **Per-tool semantics.** ``extraction_method`` is a categorical
  string the tool chooses from the registry below. The FE renders a
  per-method human label; new adopters MUST add their label here so
  the registry stays the single source of truth.
- **``confidence`` is per-tool, but bounded.** The schema enforces
  0.0–1.0 so the FE chip's color band has a fixed scale. Each tool
  decides whether to populate it; raw fetches typically leave it
  null (truthiness is synthesis_grounding's concern).
- **Testable timestamps.** ``_now_iso`` is patchable so golden
  snapshots stay deterministic — production uses real UTC, tests
  monkeypatch a fixed string.

Where it lives:

- ``app/core/`` because provenance is cross-domain: scraping and
  people (Apollo) both produce it. The chat layer surfaces it but is
  not the owner.
- ``app/people/provenance.py`` imports it. Nothing in the agent layer
  attaches it to a tool result yet.

Registered ``extraction_method`` labels
----------------------------------------

Add a row here whenever a new handler adopts provenance.

+----------------------------------+-----------------------------------------------+------------------------------+
| Label                            | Handler(s)                                    | Trust profile                |
+==================================+===============================================+==============================+
| ``direct_fetch``                 | ``fetch_page``                                | Raw HTTP byte fetch          |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``stealth_browser``              | ``fetch_with_scrapling``                      | Stealth browser render       |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``ai_extracted``                 | ``ai_scrape_page``                            | LLM re-projection of DOM     |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``facebook_ad_library``          | ``social_ads``, ``social_company_ads``,       | Meta Ad Library (official)   |
|                                  | ``social_ad_details``,                        |                              |
|                                  | ``social_advertiser_search`` (fb)             |                              |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``linkedin_ad_library``          | ``social_ads``, ``social_company_ads``        | LinkedIn Ad Library          |
|                                  | (linkedin)                                    | (official)                   |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``google_ad_library``            | ``social_advertiser_search``,                 | Google Ad Transparency       |
|                                  | ``social_company_ads`` (google)               | (official)                   |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``scrapecreators_tiktok``        | ``social_*`` platform=tiktok,                 | ScrapeCreators (third-party) |
|                                  | ``social_hashtag``                            |                              |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``scrapecreators_instagram``     | ``social_*`` platform=instagram,              | ScrapeCreators (third-party) |
|                                  | ``social_user_reels``                         |                              |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``scrapecreators_youtube``       | ``social_*`` platform=youtube                 | ScrapeCreators (third-party) |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``scrapecreators_x``             | ``social_*`` platform=twitter/x,             | ScrapeCreators (third-party) |
|                                  | ``twitter_community``                         |                              |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``scrapecreators_bluesky``       | ``social_*`` platform=bluesky                 | ScrapeCreators (third-party) |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``scrapecreators_linkedin``      | ``social_*`` platform=linkedin                | ScrapeCreators (third-party) |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``scrapecreators_facebook``      | ``social_*`` platform=facebook                | ScrapeCreators (third-party) |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``scrapecreators_pinterest``     | ``social_*`` platform=pinterest,              | ScrapeCreators (third-party) |
|                                  | ``social_user_boards``, ``social_board_info`` |                              |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``scrapecreators_threads``       | ``social_*`` platform=threads                 | ScrapeCreators (third-party) |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``scrapecreators_twitch``        | ``social_*`` platform=twitch                  | ScrapeCreators (third-party) |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``scrapecreators_snapchat``      | ``social_*`` platform=snapchat                | ScrapeCreators (third-party) |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``scrapecreators_truthsocial``   | ``social_*`` platform=truthsocial             | ScrapeCreators (third-party) |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``x_api``                        | ``x_search_tweets``, ``x_user_info``,         | Native X API (official)      |
|                                  | ``x_user_tweets``, ``x_tweet_info``           |                              |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``bluesky_api``                  | ``bluesky_user_info``, ``bluesky_user_posts``,| AT Protocol (official)       |
|                                  | ``bluesky_search_posts``                      |                              |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``youtube_data_api``             | ``youtube_search_videos``,                    | YouTube Data API v3          |
|                                  | ``youtube_channel_info``,                     | (official)                   |
|                                  | ``youtube_channel_videos``,                   |                              |
|                                  | ``youtube_video_info``                        |                              |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``github_api``                   | ``github_search_users``, ``github_user_info`` | GitHub API (official)        |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``google_maps``                  | ``search_places``                             | Google Maps via Serper       |
|                                  |                                               | (third-party)                |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``trustpilot``                   | ``mine_reviews`` (source=trustpilot)          | Trustpilot public profile    |
|                                  |                                               | (rendered via Firecrawl)     |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``google_reviews``               | ``mine_reviews`` (source=google)              | Google Maps reviews via      |
|                                  |                                               | Serper (third-party), a      |
|                                  |                                               | different population from    |
|                                  |                                               | ``trustpilot``, never pooled |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``apollo_verified``              | ``enrich_email``,                             | Apollo.io REVEAL — the       |
|                                  | ``reveal_all_pending``                        | record the provider          |
|                                  |                                               | maintains, paid for;         |
|                                  |                                               | confidence mapped from       |
|                                  |                                               | email_status                 |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``apollo_search_index``          | ``find_decision_maker``                       | Apollo.io SEARCH INDEX — a   |
|                                  | (free preview rows)                           | snapshot of unknown age,     |
|                                  |                                               | measured stale against the   |
|                                  |                                               | reveal (#1911), so it        |
|                                  |                                               | carries NO ``scraped_at``    |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``email_mx_check``               | ``verify_email``                              | Syntax + DNS/MX only, no     |
|                                  |                                               | mailbox probe — capped at    |
|                                  |                                               | 0.6 (below apollo probable)  |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``jobspy``                       | ``search_jobs``,                              | JobSpy multi-board fallback  |
|                                  | ``search_jobs_broad``                         | (row has no ``site`` field)  |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``indeed``                       | ``search_jobs``, ``search_jobs_broad``        | JobSpy — Indeed board        |
|                                  | (row ``site='indeed'``)                       |                              |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``linkedin``                     | ``search_jobs``, ``search_jobs_broad``        | JobSpy — LinkedIn board      |
|                                  | (row ``site='linkedin'``)                     |                              |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``zip_recruiter``                | ``search_jobs``, ``search_jobs_broad``        | JobSpy — ZipRecruiter board  |
|                                  | (row ``site='zip_recruiter'``)                |                              |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``subito``                       | ``search_subito_lavoro``                      | Subito.it scrape (IT)        |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``monster_it``                   | ``search_monster_it``                         | Monster.it scrape (IT)       |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``adzuna``                       | ``search_adzuna``                             | Adzuna API (aggregator,      |
|                                  |                                               | delayed sync)                |
+----------------------------------+-----------------------------------------------+------------------------------+
| ``author_timeline_gate``         | ``find_proven_posts``                         | Named-author timelines       |
|                                  |                                               | (X / Bluesky / LinkedIn      |
|                                  |                                               | company page) screened by    |
|                                  |                                               | an engagement + window       |
|                                  |                                               | gate — NOT a keyword search  |
+----------------------------------+-----------------------------------------------+------------------------------+
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


def _now_iso() -> str:
    """ISO 8601 UTC timestamp for the moment the provenance is built.

    Patchable in tests via ``monkeypatch.setattr(
    'app.core.provenance._now_iso', lambda: '2026-01-01T00:00:00+00:00')``
    so syrupy snapshots stay deterministic.
    """
    return datetime.now(UTC).isoformat()


class Provenance(BaseModel):
    """Where data came from, when, how, and how trustworthy.

    All fields optional — a partial Provenance is better than none.
    The FE chip renders whatever subset is present:

    - ``source_url`` → clickable source link
    - ``scraped_at`` → "scraped 2h ago" freshness badge
    - ``extraction_method`` → method label (e.g. "verified via Apollo")
    - ``confidence`` → 0-1 chip color (green/yellow/red bands per FE)

    Stored as part of the tool-result dict the LLM sees, but framed
    as metadata: the LLM is instructed (via system prompt updates,
    follow-up) to cite the ``source_url`` when answering and to
    surface ``confidence`` qualitatively (e.g. "highly confident:
    verified contact") rather than as a raw number.
    """

    # Allow forward-compatible extras — adding e.g. ``verified_at`` or
    # ``provider_id`` later must not require a schema migration on
    # every consumer.
    model_config = ConfigDict(extra='allow')

    source_url: str | None = None
    scraped_at: str | None = None
    extraction_method: str | None = None
    # Bounded 0.0-1.0 so the FE chip's color bands (e.g. green/yellow/red)
    # have a fixed scale to map against. Direct construction
    # (``Provenance(confidence=1.5)``) also rejects, not just the helper.
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


def build_provenance(
    *,
    source_url: str | None = None,
    extraction_method: str | None = None,
    confidence: float | None = None,
    scraped_at: str | None = None,
) -> Provenance:
    """Construct a :class:`Provenance` with ``scraped_at`` defaulted to now.

    Kwargs-only by design — provenance values often look similar at
    the call site (``url``, ``url``, ``url``…), so forcing keyword
    arguments prevents bug-prone positional confusion. Override
    ``scraped_at`` only when backfilling a historical record (e.g.
    KG entity recall surfacing data scraped weeks ago).

    **The now() default is a claim, and only a fetch is entitled to
    make it.** It is true for the families that read a page in the
    same call — the byte the user sees was on the wire a moment ago.
    It is false for anything served out of a provider's index, where
    the instant we asked is not the instant the value was collected:
    #2020 found ``find_decision_maker`` preview rows badged "scraped
    just now" off an Apollo snapshot that #1911 measured months
    behind the paid record. A caller that cannot name the collection
    instant must not reach for this default — it builds
    :class:`Provenance` directly and leaves ``scraped_at`` unset, so
    the block says nothing rather than something false. The FE is
    already built for that silence: the chip drops the freshness
    suffix, the results table drops the age off the source cell, and
    the table header renders no "collected …" line at all.
    """
    return Provenance(
        source_url=source_url,
        scraped_at=scraped_at if scraped_at is not None else _now_iso(),
        extraction_method=extraction_method,
        confidence=confidence,
    )
