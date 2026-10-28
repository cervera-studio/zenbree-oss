"""A LinkedIn DM to a connected account, from the Unipile webhook (app/linkedin/api.py)."""

from app.core.db import get_conn


def message_received(payload: dict) -> dict:
    """A LinkedIn DM to a connected account (#86). Unipile also notifies the account's OWN messages
    (every DM a campaign sends), and those must never count as a reply. Adapted from the earlier implementation
    ``linkedin_webhooks.py``."""
    info = payload.get('account_info')
    info = info if isinstance(info, dict) else {}
    sender = payload.get('sender')
    sender = sender if isinstance(sender, dict) else {}
    sender_id = str(sender.get('attendee_provider_id') or '')
    own = str(info.get('user_id') or '')
    if not sender_id or sender_id == own or payload.get('is_sender') in (True, 1, 'true') or payload.get('is_event'):
        return {'handled': False, 'event': 'message_received', 'reason': 'outgoing_or_event'}
    with get_conn() as conn:
        row = conn.execute(
            'SELECT u.user_id, ARRAY(SELECT ps.project_id::text FROM project_senders ps WHERE ps.sender_id = u.id)'
            " AS projects FROM user_oauth_connections u WHERE u.provider = 'unipile' AND u.provider_account_id = %s"
            " AND status <> 'disconnected'",
            (str(payload.get('account_id') or ''),),
        ).fetchone()
    message_id = str(payload.get('message_id') or '')
    if row is None or not message_id:
        return {'handled': False, 'event': 'message_received'}
    from app.outreach import replies

    done = replies.record(
        str(row['user_id']),
        project_ids=list(row['projects']),
        channel='linkedin',
        provider_message_id=message_id,
        sender=str(sender.get('attendee_profile_url') or ''),
        sender_name=str(sender.get('attendee_name') or ''),
        text=str(payload.get('message') or payload.get('text') or ''),
        provider_id=sender_id,
    )
    return {'handled': True, 'event': 'message_received', **done}
