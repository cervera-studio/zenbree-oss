"""The sends a stopped person still had waiting: a reply, an opt-out or an exclusion skips them all with its code."""

from __future__ import annotations

from app.outreach import send_states

_OPEN = send_states.guard('skipped', frm=('awaiting_approval', 'approved', 'failed'))  # a send in flight is left alone


def skip(conn, target_ids: list, code: str) -> None:
    if not target_ids:
        return
    conn.execute(
        f"UPDATE outreach_campaign_sends SET status = 'skipped', error_code = %s WHERE target_id = ANY(%s) AND {_OPEN}",
        (code, target_ids),
    )
