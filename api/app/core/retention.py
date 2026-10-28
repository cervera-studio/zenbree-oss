"""The daily deletes of rows that only matter for a while (SystemReason.RETENTION, run by app.worker)."""

import structlog

from app.core.db import get_conn

logger = structlog.get_logger()


def purge() -> dict:
    with get_conn() as conn:
        deleted = conn.execute("DELETE FROM llm_usage WHERE created_at < now() - interval '30 days'").rowcount
        # The web counts these per address over 24 h and deletes an address's old rows only when it mails it again.
        sends = conn.execute("DELETE FROM auth_email_sends WHERE sent_at < now() - interval '24 hours'").rowcount
    logger.info('retention.purge', llm_usage=deleted, auth_email_sends=sends)
    return {'deleted': deleted, 'auth_email_sends': sends}
