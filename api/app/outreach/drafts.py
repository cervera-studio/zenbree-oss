"""Draft campaigns from a list of people: the one enrollment path for the campaign route and the
chat's ``draft_campaign`` tool. Nothing is sendable until the user activates the campaign."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Literal

from app.billing.row_charges import finalize
from app.exclusions.rules import is_excluded
from app.lists.rows import DELIVERED_SQL
from app.outreach import holds, sender_pool
from app.outreach.freeze import freeze
from app.outreach.target_insert import insert_draft
from app.scheduling.runner import enrollment_context, enrollment_verdict

# ponytail: one enrollment from one list, first 1000 people (the earlier implementation MAX_TARGETS); no later roster edits.
MAX_TARGETS = 1000


@dataclass(frozen=True)
class Draft:
    id: str
    name: str
    enrolled: int  # people added by this call
    target_count: int  # people on the draft now


def enrolled_status(campaign) -> Literal['draft', 'active']:
    """The status a person gets on entering ``campaign``: a running campaign contacts them (active, or paused until it
    resumes), a draft keeps them for review until activation. Every enrollment into an existing campaign uses it."""
    return 'draft' if campaign['status'] == 'draft' else 'active'


def enroll_rows(
    conn,
    campaign,
    owner_id,
    rows,
    context,
    *,
    room: int,
    source: str = 'manual',
    signal_id=None,
    status: Literal['draft', 'active'] = 'draft',
    collect: list[str] | None = None,
) -> tuple[list[str], Counter]:
    """Insert ``rows`` (``id``, ``cells``) as targets, at most ``room``, by the one enrollment rule. Returns
    the enrolled row ids (and, in ``collect``, the new target ids) and a count of why the others were left out: ``excluded``, ``duplicate`` (already a
    target, replied, or contacted before), ``no_contact`` (no usable email or LinkedIn), ``in_other_campaign``, ``held_by_instantly`` (Instantly holds the email),
    ``stopped`` (a stop of the person in Instantly holds every channel) and ``over_limit``."""
    enrolled: list[str] = []
    targets: list[str] = []
    left_out: Counter = Counter()
    for row in rows:
        cells = row['cells']
        if len(enrolled) >= room:
            left_out['over_limit'] += 1
            continue
        # Same rules as refill: exclusions, replied, prior contacts; a person in another
        # active/paused campaign ('hold') is left out rather than queued.
        verdict, email, linkedin = enrollment_verdict(cells, campaign, context)
        # A stop names the person's own reason over a duplicate or another campaign: they never join, wherever
        # they are. Instantly holding the email only matters to someone who could join otherwise.
        hold = holds.for_campaign(conn, campaign, email, linkedin) if email or linkedin else None
        if verdict == 'skip' and is_excluded(context[2], cells):
            left_out['excluded'] += 1
        elif hold and (verdict == 'enroll' or hold in holds.STOP_REASONS):
            left_out[holds.SKIP_REASON[hold]] += 1
        elif verdict == 'hold':
            left_out['in_other_campaign'] += 1
        elif verdict == 'skip':
            left_out['duplicate' if email or linkedin else 'no_contact'] += 1
        else:
            inserted = insert_draft(conn, campaign, owner_id, cells, email, row['id'], source, signal_id)
            if inserted:
                enrolled.append(str(row['id']))
                targets.append(inserted)
            else:
                left_out['duplicate'] += 1
    if status == 'active':
        freeze(conn, campaign, targets)
    if collect is not None:
        collect.extend(targets)
    return enrolled, left_out


def draft_from_list(
    conn, owner_id: str, list_id: str, name: str, *, reuse: bool, block_active_duplicates: bool = True
) -> Draft:
    """Enroll ``list_id``'s people as draft targets of a campaign stamped with that list.

    ``reuse``: continue the newest draft of the list (a repeated chat call adds nothing twice);
    otherwise always open a new campaign. The caller has checked that the list is a people list of the owner's
    organization; the campaign joins that organization and the list's project.
    """
    # Serialises with saved-search refill, so two enrollments see each other's targets.
    conn.execute(
        'SELECT id FROM projects WHERE id = (SELECT project_id FROM lists WHERE id = %s) FOR NO KEY UPDATE', (list_id,)
    )
    campaign = (
        conn.execute(
            "SELECT * FROM outreach_campaigns WHERE owner_id = %s AND list_id = %s AND status = 'draft'"
            ' ORDER BY created_at DESC LIMIT 1',
            (owner_id, list_id),
        ).fetchone()
        if reuse
        else None
    )
    if campaign is None:
        campaign = conn.execute(
            'INSERT INTO outreach_campaigns (organization_id, project_id, owner_id, name, list_id, block_active_duplicates)'
            ' SELECT organization_id, project_id, %s, %s, id, %s FROM lists WHERE id = %s RETURNING *',
            (owner_id, name, block_active_duplicates, list_id),
        ).fetchone()
        sender_pool.preselect(conn, campaign)
    cid = campaign['id']
    have = conn.execute(
        'SELECT count(*) AS n FROM outreach_campaign_targets WHERE campaign_id = %s', (cid,)
    ).fetchone()['n']
    context = enrollment_context(conn, campaign)
    # ponytail: no row lock, since FOR UPDATE on up to 1000 rows risks a deadlock with the turn and its settle.
    # A settle that refunds a row made unfinished between this read and the insert leaves its email in the target.
    rows = conn.execute(
        f'SELECT r.id, r.cells FROM list_rows r WHERE r.list_id = %s AND {DELIVERED_SQL}'
        ' ORDER BY r.relevance_score DESC NULLS LAST, r.seq',
        (list_id,),
    ).fetchall()
    enrolled, _ = enroll_rows(conn, campaign, owner_id, rows, context, room=MAX_TARGETS - have)
    finalize(conn, enrolled)
    return Draft(str(cid), campaign['name'], len(enrolled), have + len(enrolled))


def drop_blocked_drafts(conn, campaign: dict, only: set | None = None) -> list:
    """Enrollment rules again, at activation or approval: since a draft was made someone may have replied, been
    contacted, been activated in another campaign, or come under the hold (``app.outreach.holds``). Deletes the
    campaign's draft people that the rules now refuse (``only``: just those target ids) and returns the ids of the
    drafts that stay."""
    targets, sent, rules, ids = enrollment_context(conn, campaign)
    others = [t for t in targets if t['campaign_id'] != campaign['id']]  # a person never blocks themselves
    drafts = [
        t
        for t in targets
        if t['campaign_id'] == campaign['id'] and t['status'] == 'draft' and (only is None or t['id'] in only)
    ]
    blocked = [
        t['id']
        for t in drafts
        if enrollment_verdict(t, campaign, (others, sent, rules, ids))[0] != 'enroll'
        or holds.for_campaign(conn, campaign, t['email'], t['linkedin_url'])
    ]
    if blocked:
        conn.execute('DELETE FROM outreach_campaign_targets WHERE id = ANY(%s)', (blocked,))
    return [t['id'] for t in drafts if t['id'] not in blocked]
