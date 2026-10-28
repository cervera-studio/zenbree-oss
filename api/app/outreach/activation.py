"""What stops a draft campaign from going live (#84, #935): every channel of its steps needs something that can send
it. The first refusal is raised, in a fixed order the builder and the tests rely on."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.errors_http import api_error
from app.outreach import crm_tasks, dispatcher, pool_state


def _linkedin_connected(conn, project_id) -> bool:
    return bool(
        conn.execute(
            'SELECT 1 FROM project_senders ps JOIN user_oauth_connections u ON u.id = ps.sender_id'
            " WHERE ps.project_id = %s AND u.provider = 'unipile' AND u.status = 'connected'",
            (project_id,),
        ).fetchone()
    )


def check(conn, campaign: dict) -> None:
    channels = {s['channel'] for s in campaign['steps']}
    if 'email' in channels and not pool_state.can_send(pool_state.accounts(conn, campaign, datetime.now(UTC))):
        raise api_error(422, 'CAMPAIGN.NO_SENDER', 'pick a connected mailbox to send from')
    # A step the dispatcher cannot run would stop every person at it for good.
    if unsupported := channels - set(dispatcher.SENDABLE_CHANNELS):
        raise api_error(422, 'CAMPAIGN.CHANNEL_UNAVAILABLE', f'{sorted(unsupported)[0]} steps cannot be sent yet')
    if channels.intersection(dispatcher.LINKEDIN_CHANNELS) and not _linkedin_connected(conn, campaign['project_id']):
        raise api_error(422, 'CAMPAIGN.NO_LINKEDIN', 'connect LinkedIn to run LinkedIn steps')
    if crm_tasks.CHANNEL in channels and not crm_tasks.can_run(conn, campaign['owner_id']):
        raise api_error(422, 'CAMPAIGN.NO_HUBSPOT', 'connect HubSpot and allow it to write, to run task steps')
