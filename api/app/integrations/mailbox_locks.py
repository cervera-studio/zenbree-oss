"""Row locks that a mailbox change takes in the order every connect, link and release takes them (#1085, #1099)."""


def lock_mailbox_twins(conn, org, sender_id) -> None:
    """Lock ``sender_id`` and the organization's rows of its mailbox in id order, before the unlink writes: its
    reconcile moves pins onto a twin, so it needs that row after its own, and a connect or link of the same mailbox
    locks the twins in id order (#1099)."""
    conn.execute(
        """SELECT 1 FROM user_oauth_connections u
           WHERE u.organization_id = %(org)s
             AND (u.id = %(sender)s OR u.provider IN ('google', 'microsoft') AND EXISTS (
                  SELECT 1 FROM user_oauth_connections s
                  WHERE s.id = %(sender)s AND s.provider = u.provider AND mailbox_address(s.account_label) <> ''
                    AND mailbox_address(s.account_label) = mailbox_address(u.account_label)))
           ORDER BY u.id FOR UPDATE""",
        {'org': org, 'sender': sender_id},
    )
