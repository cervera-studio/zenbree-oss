"""The client's own website domain as an exclusion: never prospect the client itself (origin ``own_domain``)."""

from __future__ import annotations

from app.core.db import get_conn
from app.exclusions.rules import normalise
from app.lists.rows import norm_host


def sync_own_domain(project_id: str, user_id: str, previous_website: str | None, website: str | None) -> None:
    """Follow a website change. Same lock as the profile's rules (``source = 'onboarding'``): a manual add or an
    opt-out claims the row, and a deleted one is not brought back until the website changes again."""
    if website == previous_website:
        return
    domain = normalise('domain', norm_host(website))
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM exclusions WHERE project_id = %s AND source = 'onboarding' AND origin = 'own_domain'",
            (project_id,),
        )
        if domain:
            conn.execute(
                'INSERT INTO exclusions (project_id, user_id, kind, value, source, origin)'
                " VALUES (%s, %s, 'domain', %s, 'onboarding', 'own_domain') ON CONFLICT (project_id, kind, value) DO UPDATE SET origin = 'own_domain'"
                " WHERE exclusions.source = 'onboarding' AND exclusions.origin = 'profile'",
                (project_id, user_id, domain),
            )
