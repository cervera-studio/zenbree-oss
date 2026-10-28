"""What a reader of row cells sees (#416): never a cell whose charge is still held (``app.billing.row_charges``)
while the job that charged it runs, so no one reads, exports or researches a cell the turn may refund."""

from app.billing.row_charges import PENDING_KEYS_SQL
from app.lists.lookups import CHARGES_SQL, Lookups, email_lookup, phone_lookup
from app.lists.rows import DELIVERED_SQL, public_cells

# Use on ``list_rows r`` wherever cells leave the database for a user or a model.
VISIBLE_CELLS_SQL = f'(r.cells - {PENDING_KEYS_SQL})'
# #120: why the HubSpot read gate holds this person now, as the dispatcher reads it (app.crm.gate);
# null when nothing holds them, the owner has no HubSpot connected, or the hold has lapsed.
CRM_HOLD_SQL = (
    "SELECT jsonb_build_object('reason', g.reason, 'until', g.hold_until) FROM crm_gate_checks g"
    ' JOIN lists l ON l.user_id = g.owner_id'
    " WHERE l.id = r.list_id AND g.email = lower(btrim(r.cells->>'email'))"
    ' AND g.reason IS NOT NULL AND (g.hold_until IS NULL OR g.hold_until > now())'
    ' AND EXISTS (SELECT 1 FROM user_oauth_connections h WHERE h.user_id = g.owner_id'
    " AND h.provider = 'hubspot' AND h.status IN ('connected', 'error'))"
)
# Use on ``list_rows r``, in a SELECT that locks nothing; after a write, ``read_row`` (tests/test_charge_reads.py).
ROW_COLS = (
    f'r.id, r.seq, {VISIBLE_CELLS_SQL} AS cells, r.cell_meta, r.relevance_score, r.score_reasons, r.excluded,'
    f' r.rating, r.rating_note, r.created_at, ({DELIVERED_SQL}) AS delivered, ({CRM_HOLD_SQL}) AS crm_hold,'
    f' {CHARGES_SQL} AS charges'
)


def with_lookups(row: dict) -> dict:
    """A ``ROW_COLS`` row as the API sends it: with the email and phone lookup state of its visible cells (#659).
    The lookups read the raw cells; the cells sent have no raw status or vendor key (``public_cells``)."""
    sent = {k: v for k, v in row.items() if k != 'charges'}  # the response contract fails on a key ListRow lacks
    lookups = Lookups(
        email=email_lookup(row['cells'], row['charges']), phone=phone_lookup(row['cells'], row['charges'])
    )
    return {**sent, 'cells': public_cells(row['cells']), 'lookups': lookups.model_dump()}


def read_row(conn, row_id, list_id) -> dict:
    """The row after a write, in its own statement: a write's lock wait re-reads the row, not row_charges."""
    row = conn.execute(f'SELECT {ROW_COLS} FROM list_rows r WHERE r.id = %s AND r.list_id = %s', (row_id, list_id))
    return with_lookups(row.fetchone())
