from __future__ import annotations

from app.people.person_name import first_and_last

_INSERT = """
INSERT INTO outreach_campaign_targets (campaign_id, owner_id, email, linkedin_url, first_name, last_name, company_name,
                                       role, source, signal_id, row_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, (SELECT id FROM list_rows WHERE id = %s))
ON CONFLICT DO NOTHING RETURNING id::text AS id"""


def insert_draft(
    conn, campaign: dict, owner_id, cells: dict, email: str, row_id, source: str, signal_id=None
) -> str | None:
    row = conn.execute(
        _INSERT,
        (
            campaign['id'],
            owner_id,
            email[:320] or None,
            str(cells.get('linkedin_url') or '').strip()[:500],
            *first_and_last(cells),
            str(cells.get('company_name') or '')[:300],
            str(cells.get('title') or '')[:300],
            source,
            signal_id,
            row_id,
        ),
    ).fetchone()
    return row['id'] if row else None
