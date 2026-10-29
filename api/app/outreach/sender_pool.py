"""The mailbox accounts a campaign sends from (#935). The only writer of ``outreach_campaign_senders``; an unlink, a
member release and an account delete remove pool rows by cascade."""

from __future__ import annotations

from collections.abc import Sequence

MAX_ACCOUNTS = 20

_CONVERSATION = """EXISTS (SELECT 1 FROM outreach_campaign_targets t WHERE t.campaign_id = c.id AND t.sender_id = ps.sender_id
                  AND t.status IN ('active', 'paused'))"""
_LIVE = "c.status NOT IN ('stopped', 'completed')"
_CONNECTED = """u.status = 'connected' AND u.provider IN ('google', 'microsoft')
  AND u.id IN (SELECT ps.sender_id FROM project_senders ps WHERE ps.project_id = %(project)s)"""
_INSERT = """
INSERT INTO outreach_campaign_senders (campaign_id, sender_id, project_id, provider, organization_id)
SELECT c.id, ps.sender_id, ps.project_id, ps.provider, ps.organization_id
FROM outreach_campaigns c JOIN project_senders ps ON ps.project_id = c.project_id"""


def lock_campaigns(conn, project_id) -> None:
    """Take the lock order of a pool pick, campaign before account, before a link locks an account. The two then wait
    for each other's commit instead of deadlocking. The client's row comes first, as its delete locks it before its
    campaigns."""
    conn.execute('SELECT 1 FROM projects WHERE id = %s FOR KEY SHARE', (project_id,))
    conn.execute(
        f'SELECT 1 FROM outreach_campaigns c WHERE c.project_id = %s AND {_LIVE} ORDER BY c.id FOR KEY SHARE',
        (project_id,),
    )


def sync_linked(conn, project_id, sender_id) -> None:
    """Pool the account the client just linked in each live campaign of the client where a person still in the
    sequence has a conversation on it, so a held conversation resumes. No other pool gains it: a person picks
    accounts by id. The caller ran ``lock_campaigns``."""
    conn.execute(
        f"""{_INSERT} WHERE ps.project_id = %(project)s AND ps.sender_id = %(sender)s AND {_LIVE}
             AND ps.provider IN ('google', 'microsoft') AND {_CONVERSATION}
           ON CONFLICT DO NOTHING""",
        {'project': project_id, 'sender': sender_id},
    )


def pick(conn, campaign_id, sender_ids: Sequence) -> None:
    """The pool is exactly these accounts. The caller holds the campaign row lock and checked that each is a mailbox
    account linked to the campaign's client."""
    args = {'campaign': campaign_id, 'ids': list(sender_ids)}
    conn.execute(
        'DELETE FROM outreach_campaign_senders WHERE campaign_id = %(campaign)s AND sender_id <> ALL(%(ids)s)', args
    )
    conn.execute(f'{_INSERT} WHERE c.id = %(campaign)s AND ps.sender_id = ANY(%(ids)s) ON CONFLICT DO NOTHING', args)


def preselect(conn, campaign: dict) -> None:
    """The accounts are locked before the insert checks the client's links: the order of a link, which locks the
    account and then replaces its link row."""
    args = {'campaign': campaign['id'], 'project': campaign['project_id'], 'max': MAX_ACCOUNTS}
    conn.execute(f'SELECT 1 FROM user_oauth_connections u WHERE {_CONNECTED} ORDER BY u.id FOR KEY SHARE', args)
    conn.execute(
        f"""{_INSERT} JOIN user_oauth_connections u ON u.id = ps.sender_id
           WHERE c.id = %(campaign)s AND {_CONNECTED} ORDER BY u.created_at LIMIT %(max)s""",
        args,
    )
