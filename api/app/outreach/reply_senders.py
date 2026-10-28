"""Internal senders cannot speak for a prospect by quoting a campaign email (#1173)."""

from app.crm.contacts import company_domain


def same_owner(sender: str, owner_email: str) -> bool:
    """The owner or their business domain, never strangers sharing a personal mailbox provider."""
    sender, owner_email = sender.strip().lower(), owner_email.strip().lower()
    domain = company_domain(owner_email)
    return bool(sender and (sender == owner_email or (domain and sender.rpartition('@')[2] == domain)))


def is_internal(conn, organization_id: str, owner_id: str | None, sender: str) -> bool:
    row = conn.execute(
        """SELECT (SELECT email FROM "user" WHERE id = %s) AS owner_email,
                  EXISTS (SELECT 1 FROM member m JOIN "user" u ON u.id = m."userId"
                          WHERE m."organizationId" = %s AND lower(btrim(u.email)) = %s) AS member""",
        (owner_id, organization_id, sender.strip().lower()),
    ).fetchone()
    return row['member'] or same_owner(sender, row['owner_email'] or '')
