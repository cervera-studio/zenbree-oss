from __future__ import annotations

from uuid import UUID


def max_entry_credits(conn, segment_id: UUID | str, identity_count: int) -> int:
    """The most the automations that read this segment can spend if ``identity_count`` people enter. No automation
    can read a segment yet, so 0. #1029 replaces this body with the sum of ``max_credits`` from
    ``app.automations.preview`` over the automations that use the segment."""
    return 0
