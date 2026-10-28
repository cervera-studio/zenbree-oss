"""The job-posting signal (#647): what an account's open postings say, compared with what was already seen."""

from __future__ import annotations

from datetime import UTC, datetime

from app.people.apollo_models import JobPosting

TYPE = 'job_posting'
# A fresh posting; older ones start lower through the decay (``signal_score`` in the watchlists migration).
SCORE = 100.0


def matches(posting: JobPosting, job_titles: list[str]) -> bool:
    """A posting is in a watched function when its title contains one of the watched titles."""
    title = posting.title.lower()
    return any(wanted.strip().lower() in title for wanted in job_titles if wanted.strip())


def key(posting: JobPosting) -> str:
    """What makes two observations the same posting: its link, else its title and place."""
    if posting.url:
        return posting.url.strip()[:1000]
    return '|'.join((posting.title.strip().lower(), (posting.city or '').lower(), (posting.country or '').lower()))[
        :1000
    ]


def occurred_at(posting: JobPosting, now: datetime) -> datetime:
    """When the posting went up, so one that is months old starts decayed. Now when the source does not say, or
    says a moment that has not come."""
    try:
        posted = datetime.fromisoformat((posting.posted_at or '').replace('Z', '+00:00'))
    except ValueError:
        return now
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=UTC)
    return min(posted, now)
