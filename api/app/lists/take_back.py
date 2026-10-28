"""Cells a refund takes back from a list row (#747, owner decision 2026-10-16): the credit and the data go together, so a
refund never leaves the user what it gave back. The list row's writer for this, beside ``rows.py`` (at its size limit),
as ``row_charges.refund_unfinished`` takes back an unfinished turn's cells: the bought keys leave ``cells`` and
``cell_meta`` together, and the refunded charge makes ``lookups`` read the email as ``refunded``."""

from collections.abc import Sequence


def take_back(conn, row_id: str, keys: Sequence[str], email: str) -> bool:
    """In the refund's transaction, with the row locked by it: ``keys`` leave that one row, only while its email is
    still ``email``. False when it is not (changed or found again since): nothing is touched. Calling it again is a
    no-op, since the address is gone."""
    return bool(
        conn.execute(
            'UPDATE list_rows SET cells = cells - %s::text[], cell_meta = cell_meta - %s::text[]'
            " WHERE id = %s AND lower(btrim(cells->>'email')) = %s",
            (list(keys), list(keys), row_id, email.strip().lower()),
        ).rowcount
    )
