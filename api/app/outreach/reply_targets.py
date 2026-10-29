"""Which campaign targets an inbound reply answers (#86, #414, #467).

A reply address names a campaign, or in its old form an owner only, and an owner can run campaigns to the same
address in several organizations, which keep them once the owner deletes their account. A reply is filed, stopped
and announced in one of them: the campaign's that ``email_scope`` names, else the one ``scope_to_organization``
picks, over plain rows, apart from the SQL that finds them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

# Same normalisation as the target LinkedIn unique index in 20261005000000_baseline.sql.
LINKEDIN_KEY = (
    "lower(regexp_replace(split_part(split_part(btrim({}), '?', 1), '#', 1),"
    " '^https?://(www\\.)?|^www\\.|/+$', '', 'gi'))"
)

TARGET_COLUMNS = 't.id, t.campaign_id, t.owner_id, t.email, t.linkedin_url, c.project_id, c.organization_id'
_EPOCH = datetime.min.replace(tzinfo=UTC)


def scope_to_organization(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The rows of the one organization a reply belongs to, best first, so ``[0]`` files and announces it.

    A quoted message is the surest sign of what the reply answers, the newest quoted send first. With no quote
    the person's newest mail decides, then the newest target.

    Each row has ``organization_id``, ``by_reference`` (the reply quotes one of its sends), ``quoted_at`` (the
    time of the quoted send), ``last_sent_at`` (the delivery of its newest email, ``None`` when none) and
    ``created_at``.
    """
    ordered = sorted(rows, key=lambda r: r['id'])
    ordered.sort(key=lambda r: r['created_at'], reverse=True)
    ordered.sort(key=lambda r: r['last_sent_at'] or _EPOCH, reverse=True)
    ordered.sort(key=lambda r: (r['by_reference'], r['quoted_at'] or _EPOCH), reverse=True)
    return [r for r in ordered if r['organization_id'] == ordered[0]['organization_id']] if ordered else []


def in_organization(rows: Sequence[Mapping[str, Any]], named: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    """The rows a reply is filed and stops in: the named campaign's organization, else the ranking's pick.

    ponytail: the ranking serves the old owner-form address, which mail sent before the campaign form carries, and the
    address of a deleted campaign (one deleted with its client, or a draft deleted alone). 90 days after go-live (sends
    before go-live are test sends), delete the old form's parse and the ``campaign_id is None`` branch of
    ``email_scope``; keep the ranking and ``key_scope`` for a deleted campaign's key, as its opt-out mailto never
    expires. The same PR gives an inbox answer to a message no campaign matched a reply address of its own (naming its
    project or the inbound message), never no ``Reply-To``: the person's answer would reach a send-only mailbox and
    leave the inbox untraced."""
    if named is None:
        return scope_to_organization(rows)
    return [r for r in rows if str(r['organization_id']) == named['organization_id']]


def filed_under(scoped: Sequence[Mapping[str, Any]], named: Mapping[str, Any] | None) -> tuple[Any, Any]:
    """``(target, campaign)`` a message is filed under: the named campaign, with its person when they matched."""
    if named is not None:
        return next((r['id'] for r in scoped if str(r['campaign_id']) == named['id']), None), named['id']
    return (scoped[0]['id'], scoped[0]['campaign_id']) if scoped else (None, None)


def still_there(conn, target_id: Any, campaign_id: Any) -> tuple[Any, Any]:
    """What a reply files under once it holds the project lock: an activation may have dropped the held draft it
    matched, or a draft campaign was deleted, while it waited. ``None`` for what is gone, the row ON DELETE SET NULL
    leaves when the message lands first."""
    row = conn.execute(
        'SELECT (SELECT id FROM outreach_campaign_targets WHERE id = %s) AS target,'
        ' (SELECT id FROM outreach_campaigns WHERE id = %s) AS campaign',
        (target_id, campaign_id),
    ).fetchone()
    return row['target'], row['campaign']


# A send the message quotes: by the Message-ID we wrote or by the one Gmail or Exchange gave it instead (#747).
QUOTED = '(s.message_id = ANY(%(refs)s) OR s.provider_message_id = ANY(%(refs)s))'


def _email_targets(conn, scope: str, sends: str, params: dict[str, Any]) -> list:
    """``scope`` filters the targets, ``sends`` the sends that may be quoted and that rank the targets."""
    # ponytail: no sends index starts with target_id; the plan reads an owner's sends through
    # outreach_campaign_sends_owner_created_idx, and a client's or a deleted member's (``sends`` is 'true': their
    # rows have no owner_id) through a scan of the table. Add (target_id) if the sends reach the millions.
    return conn.execute(
        f"""SELECT {TARGET_COLUMNS}, t.created_at,
                   coalesce(bool_or({QUOTED}), false) AS by_reference,
                   max(coalesce(s.delivered_at, s.created_at)) FILTER (WHERE {QUOTED}) AS quoted_at,
                   max(s.delivered_at) FILTER (WHERE s.channel = 'email') AS last_sent_at
            FROM outreach_campaign_targets t
            JOIN outreach_campaigns c ON c.id = t.campaign_id
            LEFT JOIN outreach_campaign_sends s ON s.target_id = t.id AND {sends}
            WHERE {scope}
              AND (lower(btrim(t.email)) = %(sender)s
                   OR t.id IN (SELECT target_id FROM outreach_campaign_sends s
                               WHERE {sends} AND {QUOTED}))
            GROUP BY t.id, c.id""",
        params,
    ).fetchall()


def targets(
    conn,
    owner_id: str | None,
    project_ids: list[str],
    *,
    channel: str,
    sender: str,
    provider_id: str,
    references: list[str],
    former: str | None = None,
) -> list:
    """Every target the message matches."""
    if channel == 'email':
        params = {
            'owner': owner_id,
            'projects': project_ids,
            'former': former,
            'sender': sender.lower(),
            'refs': references,
        }
        if owner_id is not None:
            return _email_targets(conn, 't.owner_id = %(owner)s', 's.owner_id = %(owner)s', params)
        # The creator is gone (#333), and with them the owner_id of their rows: a client key's people in that client
        # only; a deleted member's old key's in the campaigns they created only (``former``), in every organization.
        scope = 'c.former_owner_id = %(former)s' if former else 'c.project_id = ANY(%(projects)s::uuid[])'
        return _email_targets(conn, scope, 'true', params)
    return conn.execute(
        f"""SELECT {TARGET_COLUMNS} FROM outreach_campaigns c
            JOIN outreach_campaign_targets t ON t.campaign_id = c.id AND t.owner_id IS NOT DISTINCT FROM c.owner_id
            WHERE c.project_id = ANY(%s::uuid[]) AND ((%s <> '' AND t.linkedin_provider_id = %s)
               OR (%s <> '' AND btrim(t.linkedin_url) <> ''
                   AND {LINKEDIN_KEY.format('t.linkedin_url')} = {LINKEDIN_KEY.format('%s')}))
            ORDER BY t.created_at, t.id""",
        (project_ids, provider_id, provider_id, sender, sender),
    ).fetchall()


def newest_project(conn, owner: str) -> str | None:
    found = conn.execute(
        'SELECT coalesce((SELECT project_id FROM outreach_campaigns WHERE owner_id = %(o)s'
        '                 AND organization_id = own_organization(%(o)s) ORDER BY created_at DESC LIMIT 1),'
        '                (SELECT id FROM projects WHERE organization_id = own_organization(%(o)s)'
        '                 ORDER BY created_at LIMIT 1)) AS project_id',
        {'o': owner},
    ).fetchone()
    return str(found['project_id']) if found and found['project_id'] is not None else None


def email_scope(conn, key: str | None, campaign_id: str | None) -> tuple:
    """``(named, owner, projects, former)`` of an email reply. ``named`` is the campaign its address names, read in
    the inbound route's scope; its people are matched as an old address of the campaign would match them, through
    its creator, else its former creator, else its client, so an opt-out still stops the person everywhere.

    A deleted campaign left that key (outreach_campaign_reply_keys), whether its client's delete took it or
    ``DELETE /v1/campaigns/{id}`` took a draft: it names no campaign, and its people are matched and ranked as its old
    address would match them, so an opt-out to its mailto still stops them everywhere. An id that was never minted has
    no key and is dropped."""
    if campaign_id is None:
        return None, *key_scope(conn, str(key))
    named = conn.execute(
        'SELECT id::text, project_id::text, organization_id::text,'
        ' coalesce(owner_id, former_owner_id, project_id)::text AS key FROM outreach_campaigns WHERE id = %s',
        (campaign_id,),
    ).fetchone()
    if named:
        return named, *key_scope(conn, named['key'])
    gone = conn.execute(
        'SELECT reply_key::text AS key FROM outreach_campaign_reply_keys WHERE campaign_id = %s', (campaign_id,)
    ).fetchone()
    return (None, *key_scope(conn, gone['key'])) if gone else (None, None, [], None)


def key_scope(conn, key: str) -> tuple[str | None, list[str], str | None]:
    """``(owner, projects, former)`` a signed reply address or unsubscribe token names (#333).

    - A member: ``(owner, [], None)``, as before.
    - A client: ``(None, [project], None)``, minted for a campaign whose creator deleted their account.
    - A member who deleted their account since: ``(None, projects, key)``. Their people are matched only in the
      campaigns they created (``former_owner_id``), never another organization's nor another former member's:
      ``References`` are the sender's to write. ``projects``: those campaigns' clients, or when none is left every
      client of the organizations they belonged to (``deleted_member_orgs``), where an opt-out is still recorded.
    """
    if conn.execute('SELECT 1 FROM "user" WHERE id = %s', (key,)).fetchone():
        return key, [], None
    if conn.execute('SELECT 1 FROM projects WHERE id = %s', (key,)).fetchone():
        return None, [key], None
    rows = (
        conn.execute(
            'SELECT DISTINCT project_id::text AS id FROM outreach_campaigns WHERE former_owner_id = %s', (key,)
        ).fetchall()
        or conn.execute(
            'SELECT p.id::text FROM deleted_member_orgs d JOIN projects p ON p.organization_id = d.organization_id'
            ' WHERE d.user_id = %s',
            (key,),
        ).fetchall()
    )
    return None, [r['id'] for r in rows], key


# Campaign projects of an owner in their own organization: where an opt-out that matched none of their people
# applies. Their own: the reply address names the person, not a team, so a team's projects are never written.
OWNER_PROJECTS = (
    'SELECT DISTINCT project_id FROM outreach_campaigns WHERE owner_id = %s'
    ' AND organization_id = own_organization(owner_id)'
)
# Every project the owner's campaigns wrote from: where the unsubscribe page looks for the opt-out.
CAMPAIGN_PROJECTS = 'SELECT DISTINCT project_id FROM outreach_campaigns WHERE owner_id = %s'


def campaign_projects(conn, owner_id: str | None, projects: list[str]) -> list[str]:
    """The owner's campaign projects, or a client's own when the key named the client (#333)."""
    if owner_id is None:
        return projects
    return [str(r['project_id']) for r in conn.execute(CAMPAIGN_PROJECTS, (owner_id,))]


def is_stored(
    conn, owner: str | None, project_id: str | None, channel: str, provider_message_id: str, rfc_message_id: str = ''
) -> bool:
    """The insert's conflict key, read in the match's scope that crosses organizations. The same email also counts
    by its own Message-ID: the sending mailbox (#1165) and Resend name one reply by different provider ids.

    ponytail: the Message-ID comparison scans the owner's inbound messages for each reply (the expression index is
    on the normalised id alone); add ``(coalesce(owner_id, project_id), expr)`` if a mailbox's volume makes it slow."""
    return (
        conn.execute(
            'SELECT 1 FROM outreach_inbound_messages WHERE coalesce(owner_id, project_id) = coalesce(%s::uuid, %s::uuid)'
            ' AND channel = %s AND (provider_message_id = %s OR'
            " (btrim(%s, '<> ') <> '' AND nullif(lower(btrim(rfc_message_id, '<> ')), '') = lower(btrim(%s, '<> '))))",
            (owner, project_id, channel, provider_message_id, rfc_message_id, rfc_message_id),
        ).fetchone()
        is not None
    )
