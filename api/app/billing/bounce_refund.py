"""The email credit a hard bounce gives back (owner decision on #747, 2026-10-15): «you pay only for what arrives».

A verified address that bounces within ``REFUND_DAYS`` of the send refunds the charge that bought it, once: the charge
becomes ``refunded`` (``row_charges``), the cells it bought leave the list row (owner decision 2026-10-16: the refund
takes the data back), and the ledger shows a ``refund`` of reference type ``email_bounce``. An
address the verifier did not call verified was never promised to arrive, so its bounce refunds nothing.
"""

from datetime import datetime, timedelta

from app.billing.row_charges import refund_bounced
from app.lists.take_back import take_back

REFUND_DAYS = 7


def refund(conn, send: dict, now: datetime) -> int:
    """In the bounce's transaction. ``send``: the bounced send with its campaign's ``list_id``, ``project_id``,
    ``organization_id`` and ``owner_id``. The credits given back, 0 when nothing is due. The ledger row names the
    project, so its monthly cap counts the email as not spent (``project_month_spend``)."""
    if send['delivered_at'] is None or now - send['delivered_at'] > timedelta(days=REFUND_DAYS):
        return 0
    if not send['list_id'] or not (charge := refund_bounced(conn, send['list_id'], send['recipient'])):
        return 0  # a campaign whose list is gone has no row to take the address back from
    # The data goes with the credit (owner decision 2026-10-16): a report planted to win a refund gains nothing.
    if not take_back(conn, charge['row_id'], charge['keys'], send['recipient']):
        # unreachable while refund_bounced holds the row's lock; never refund what stays on the row
        raise LookupError('the refunded row changed under its lock')
    credits = charge['credits']
    conn.execute(
        "SELECT increment_credits(%s, %s, %s, 'refund', 'email_bounce', %s, NULL, NULL, %s)",
        (send['organization_id'], send['owner_id'], credits, send['id'], 'Email credit back: it bounced'),
    )
    conn.execute(
        "UPDATE credit_transactions SET project_id = %s WHERE reference_type = 'email_bounce' AND reference_id = %s",
        (send['project_id'], send['id']),
    )
    return credits
