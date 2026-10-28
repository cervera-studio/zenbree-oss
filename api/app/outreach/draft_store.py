"""The database steps of an inbox draft: the two run off the event loop by ``inbox_reply.draft``, and the clear
after a sent answer."""

from app.core.db import get_conn
from app.outreach import sending

_MAY_DRAFT = """(%(member_asked)s OR (m.draft_body IS NULL AND NOT EXISTS (
    SELECT 1 FROM outreach_inbox_replies r WHERE r.inbound_id = m.id AND r.status = 'sent')))"""


def source(message_id: str, regenerate: bool) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            f"""SELECT m.owner_id, m.subject, m.body, m.label, coalesce(u.name, '') AS owner_name,
                      (SELECT s.body FROM outreach_campaign_sends s WHERE s.target_id = m.target_id
                         AND s.owner_id IS NOT DISTINCT FROM m.owner_id AND s.status = 'sent' ORDER BY s.delivered_at DESC LIMIT 1)
                      AS ours
               FROM outreach_inbound_messages m LEFT JOIN "user" u ON u.id = m.owner_id
               WHERE m.id = %(id)s AND m.kind = 'reply' AND {_MAY_DRAFT} AND m.label IS NOT NULL
                 AND m.label <> 'not_interested' AND m.sender_authenticated IS DISTINCT FROM false""",
            {'id': message_id, 'member_asked': regenerate},
        ).fetchone()


def store(message_id: str, text: str, regenerate: bool) -> None:
    with get_conn() as conn:
        # A statement that waits on the settle's row lock rechecks only that row, not the answers it read before the
        # wait: lock first, so the UPDATE below reads the answers after the settle commits.
        conn.execute('SELECT 1 FROM outreach_inbound_messages WHERE id = %s FOR NO KEY UPDATE', (message_id,))
        conn.execute(
            f'UPDATE outreach_inbound_messages m SET draft_body = %(text)s WHERE m.id = %(id)s AND {_MAY_DRAFT}',
            {'text': text[: sending.MAX_BODY], 'id': message_id, 'member_asked': regenerate},
        )


def clear(conn, reply_id) -> None:
    conn.execute(
        'UPDATE outreach_inbound_messages SET draft_body = NULL'
        ' WHERE id = (SELECT inbound_id FROM outreach_inbox_replies WHERE id = %s)',
        (reply_id,),
    )
