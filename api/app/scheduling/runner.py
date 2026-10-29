"""Durable occurrences: claim once, publish rows and settle credits atomically (``run_credits``)."""

import re
from datetime import UTC, datetime, timedelta
from functools import partial

import structlog
from fastapi import HTTPException

from app.admin.settings import vendor_disabled_now
from app.agent.tools import registry
from app.audience.follow import AliasMap, alias_map, held_pair, held_values, same_person, sent_pair, with_aliases
from app.auth.middleware import creator_not_banned
from app.billing import balance, credits
from app.billing.row_charges import PENDING_KEYS_SQL
from app.billing.tab import HoldGone
from app.core import tasks
from app.core.db import get_conn
from app.core.scope import ActorGone, acting_for
from app.crm import gate
from app.exclusions.rules import Rules, is_excluded, list_rules, load_rules
from app.lists.lookups import deliverable, email_lookup
from app.outreach import holds
from app.outreach.freeze import freeze
from app.outreach.target_insert import insert_draft
from app.scheduling import run_credits
from app.scheduling.models import SearchInput
from app.scheduling.service import in_list_project, validate_links

logger = structlog.get_logger()
STALE_AFTER = timedelta(minutes=17)  # task hard limit 16m; credit reaper starts at 20m


def locked_run(conn, run_id):
    row = conn.execute('SELECT saved_search_id FROM saved_search_runs WHERE id=%s', (run_id,)).fetchone()
    if not row:
        return None, None
    search = conn.execute('SELECT * FROM saved_searches WHERE id=%s FOR UPDATE', (row['saved_search_id'],)).fetchone()
    run = conn.execute('SELECT * FROM saved_search_runs WHERE id=%s FOR UPDATE', (run_id,)).fetchone()
    return search, run


def wallet(conn, run) -> str:
    """The organization whose credits a run spends: its list's (#306). A run always has one: deleting the
    list deletes its searches and runs."""
    row = conn.execute('SELECT organization_id FROM lists WHERE id=%s', (run['config']['list_id'],)).fetchone()
    return str(row['organization_id'])


def stop(conn, run, status, code):
    run_credits.close(conn, run)
    conn.execute(
        'UPDATE saved_search_runs SET status=%s,error_code=%s,finished_at=now() WHERE id=%s', (status, code, run['id'])
    )


def active(search):
    return search and search['enabled'] and search['deleted_at'] is None


def claim(run_id):
    """The queued run, now running with its quote held (a free search holds nothing), as its owner. When the owner
    left the organization since it was queued, the organization fails the run ``creator_left``, so the beat does not
    queue it again; when they were banned, it is ``cancelled`` (#389: their searches wait for the ban to end). Then
    ``ActorGone`` goes on."""
    try:
        return _claim(run_id)
    except ActorGone as gone:
        with acting_for(gone.scope.org, 'run of an owner who left'), get_conn() as conn:
            _, run = locked_run(conn, run_id)
            # A header names who is gone; only that member's own run is closed, so a forged one cannot end a teammate's.
            if run and run['status'] == 'queued' and str(run['user_id']) == gone.scope.user:
                left = conn.execute(f'SELECT {creator_not_banned("%s")} AS ok', (run['user_id'],)).fetchone()['ok']
                stop(conn, run, *(('failed', 'creator_left') if left else ('cancelled', 'user_banned')))
        raise


def _claim(run_id):
    with get_conn() as conn:
        search, run = locked_run(conn, run_id)
        if not run or run['status'] != 'queued':
            return None
        if not active(search):
            stop(conn, run, 'cancelled', 'schedule_paused')
            return None
        try:
            config = SearchInput.model_validate(run['config'])
            validate_links(conn, run['user_id'], config.model_dump(mode='json'))
        except ValueError, HTTPException:
            # Fail closed on stale ownership/entity or malformed persisted input.
            stop(conn, run, 'failed', 'invalid_configuration')
            return None
        if not run_credits.fits(run['config']):
            stop(conn, run, 'failed', 'tool_contract_changed')
            return None
        org = wallet(conn, run)
        project = conn.execute('SELECT project_id FROM lists WHERE id=%s', (run['config']['list_id'],)).fetchone()
        tab = run_credits.hold(conn, run, credits.Payer(org, str(run['user_id']), str(project['project_id'])))
        if not isinstance(tab, credits.Refused):
            started = conn.execute(
                "UPDATE saved_search_runs SET status='running',reservation_id=%s,started_at=now() WHERE id=%s"
                ' RETURNING *',
                (tab.hold.id if tab else None, run_id),
            ).fetchone()
            return {**started, 'organization_id': org}
        # #307: the list's project at its hard monthly cap refuses the hold even with credits in the wallet.
        stop(conn, run, 'failed', 'project_cap_reached' if tab.capped else 'insufficient_credits')
    balance.changed(org, refused=True)  # credits short: auto top-up may be due
    return None


def finish(run_id, result):
    with get_conn() as conn:
        search, run = locked_run(conn, run_id)
        if not run or run['status'] != 'running':
            return
        try:
            tab = run_credits.lock(conn, run)
        except HoldGone:
            stop(conn, run, 'unknown', 'reservation_expired')
            return
        if not active(search):
            stop(conn, run, 'cancelled', 'schedule_paused')
            return
        try:
            validate_links(conn, run['user_id'], run['config'])
        except HTTPException:
            stop(conn, run, 'failed', 'invalid_configuration')  # the hold goes back, nothing is written
            return
        entity = 'person' if run['config']['tool_name'] == 'search_people' else 'company'
        if not result.ok:
            stop(conn, run, 'failed', 'search_failed')
            return
        if result.entity != entity or result.row_updates or len(result.rows) > 100:
            stop(conn, run, 'failed', 'tool_contract_changed')
            return
        rules = list_rules(conn, run['config']['list_id'])
        ids, cost = run_credits.deliver(conn, run, tab, [r for r in result.rows if not is_excluded(rules, r)], entity)
        conn.execute(
            "UPDATE saved_search_runs SET status='succeeded',credits_spent=%s,new_row_ids=%s::uuid[],"
            'pending_row_ids=%s::uuid[],refill_done=%s,finished_at=now() WHERE id=%s',
            (cost, ids, ids, not bool(run['config']['campaign_id'] and ids), run_id),
        )


async def run_search(run_id):
    run = claim(run_id)
    if not run:
        return
    if run['reservation_id']:
        balance.changed(run['organization_id'])  # the hold
    try:
        await _search(run_id, run)
    except ActorGone:  # the owner left mid-run: nothing is written, and the organization gets its hold back
        with acting_for(run['organization_id'], 'run of an owner who left'), get_conn() as conn:
            _, current = locked_run(conn, run_id)
            if current and current['status'] == 'running':
                stop(conn, current, 'failed', 'creator_left')
        raise
    finally:
        if run['reservation_id']:
            balance.changed(run['organization_id'])  # settled or released


async def _search(run_id, run):
    tool = registry.get(run['config']['tool_name'])
    try:
        if tool is None:  # the claim refused it; a deploy since may have dropped it
            raise ValueError('tool_contract_changed')
        # Recheck pause immediately before starting the sole external operation.
        with get_conn() as conn:
            search, current = locked_run(conn, run_id)
            if not current or current['status'] != 'running':
                return
            if not active(search):
                stop(conn, current, 'cancelled', 'schedule_paused')
                return
        if tool.provider and vendor_disabled_now(tool.provider):
            raise ValueError('vendor_disabled')
        result = await run_credits.call(tool, run)
        if result is None:
            with get_conn() as conn:
                _, current = locked_run(conn, run_id)
                if current and current['status'] == 'running':
                    stop(conn, current, 'failed', 'over_budget')
            return
    except Exception as exc:
        logger.error('saved_search.call_failed', run_id=str(run_id), error_type=type(exc).__name__)
        with get_conn() as conn:
            _, current = locked_run(conn, run_id)
            if current and current['status'] == 'running':
                stop(conn, current, 'failed', 'search_failed')
        return
    # Let DB errors propagate: the durable running claim prevents paid replay.
    finish(run_id, result)
    refill(run_id)


def identity(email, linkedin):
    return (
        str(email or '').strip().lower(),
        re.sub(
            r'^https?://(www\.)?|^www\.|/+$', '', str(linkedin or '').strip().lower().split('?', 1)[0].split('#', 1)[0]
        ),
    )


def enrollment_context(conn, campaign) -> tuple[list[dict], list[dict], Rules, AliasMap]:
    """What enrollment matches a person against: the targets, sent rows and exclusions of the campaign's project
    (#307: its campaigns share the project's senders), plus the campaign owner's HubSpot opt-outs. Callers hold
    the project's row lock so concurrent enrollments see each other."""
    targets = conn.execute(
        'SELECT t.*,c.status AS campaign_status,c.block_active_duplicates AS campaign_blocks,'
        ' c.steps @> \'[{"channel": "email"}]\' AS campaign_emails FROM outreach_campaign_targets t'
        ' JOIN outreach_campaigns c ON c.id=t.campaign_id AND c.owner_id IS NOT DISTINCT FROM t.owner_id'
        ' WHERE c.project_id=%s',
        (campaign['project_id'],),
    ).fetchall()
    sent = (
        conn.execute(
            'SELECT s.recipient,s.channel FROM outreach_campaign_sends s'
            " JOIN outreach_campaigns c ON c.id=s.campaign_id WHERE c.project_id=%s AND s.status='sent'",
            (campaign['project_id'],),
        ).fetchall()
        if campaign['block_prior_contacts']
        else []
    )
    # #120: an unsubscribe in HubSpot counts like the project's own exclusions.
    rules = load_rules(conn, campaign['project_id']) | with_aliases(
        conn, campaign['project_id'], gate.opted_out_rules(conn, str(campaign['owner_id']))
    )
    return targets, sent, rules, alias_map(conn, campaign['project_id'], held_values(targets, sent))  # #937


def enrollment_verdict(cells, campaign, context, *, held_by=None) -> tuple[str, str, str]:
    """``(skip|hold|enroll, email, linkedin)`` for one list row. ``hold``: in another active or paused campaign that
    holds the person (refill retries it later): ``held_by(target)``, by default this campaign's
    ``block_active_duplicates``."""
    targets, sent, rules, ids = context
    if is_excluded(rules, cells):
        return 'skip', '', ''
    email, linkedin = identity(cells.get('email'), cells.get('linkedin_url'))
    if not deliverable(email_lookup(cells, {})):
        email = ''
    if not email and not linkedin:
        return 'skip', '', ''  # A draft may lack email, but needs a durable person identity.
    mine = (('email', email), ('linkedin_url', linkedin))
    matches = [t for t in targets if same_person(ids, mine, held_pair(identity, t['email'], t['linkedin_url']))]
    if any(
        t['campaign_id'] == campaign['id'] or t['status'] in ('excluded', 'replied') or t['replied_at'] for t in matches
    ):
        return 'skip', email, linkedin
    if campaign['block_prior_contacts'] and (
        any(t['next_step'] > 1 for t in matches) or any(same_person(ids, mine, sent_pair(identity, s)) for s in sent)
    ):
        return 'skip', email, linkedin
    held_by = held_by or (lambda _: campaign['block_active_duplicates'])
    if any(t['campaign_status'] in ('active', 'paused') and held_by(t) for t in matches):
        return 'hold', email, linkedin
    return 'enroll', email, linkedin


def refill(run_id):
    with get_conn() as conn:
        owner = conn.execute(
            'SELECT c.project_id FROM saved_search_runs r'
            " LEFT JOIN outreach_campaigns c ON c.id=(r.config->>'campaign_id')::uuid WHERE r.id=%s",
            (run_id,),
        ).fetchone()
        if not owner:
            return
        # Serialise cross-campaign duplicate checks for this project's enrollments.
        conn.execute('SELECT id FROM projects WHERE id=%s FOR NO KEY UPDATE', (owner['project_id'],))
        search, run = locked_run(conn, run_id)
        if not run or run['status'] != 'succeeded' or run['refill_done'] or not active(search):
            return
        conn.execute('UPDATE saved_search_runs SET refill_checked_at=now() WHERE id=%s', (run_id,))
        config = run['config']
        campaign = conn.execute(
            'SELECT * FROM outreach_campaigns WHERE id=%s AND owner_id=%s FOR UPDATE',
            (config['campaign_id'], run['user_id']),
        ).fetchone()
        if (
            not campaign
            or campaign['status'] in ('stopped', 'completed')
            # A search saved before save() checked the project (#1086).
            or not in_list_project(conn, config['list_id'], campaign['id'])
        ):
            conn.execute('UPDATE saved_search_runs SET refill_done=true WHERE id=%s', (run_id,))
            return
        from app.outreach.drafts import MAX_TARGETS  # drafts imports this module

        if campaign['status'] != 'active':
            return  # Keep the delta during pause/draft; resume never repeats a search.
        room = (
            MAX_TARGETS
            - conn.execute(
                'SELECT count(*) AS n FROM outreach_campaign_targets WHERE campaign_id=%s', (campaign['id'],)
            ).fetchone()['n']
        )
        if (
            room <= 0
        ):  # counted under the campaign lock, held to commit; a running campaign never frees room: the found rows stay in the list, out of the campaign
            conn.execute('UPDATE saved_search_runs SET refill_done=true WHERE id=%s', (run_id,))
            return
        try:
            validate_links(conn, run['user_id'], config)  # still a member of the list's organization
        except HTTPException:
            conn.execute('UPDATE saved_search_runs SET refill_done=true WHERE id=%s', (run_id,))
            return
        locked = conn.execute(
            'SELECT r.id FROM list_rows r JOIN lists l ON l.id=r.list_id'
            " WHERE r.id=ANY(%s::uuid[]) AND r.list_id=%s AND l.user_id=%s AND l.entity='person'"
            ' AND NOT r.excluded ORDER BY r.id FOR NO KEY UPDATE OF r',  # the refund locks rows by id too: no deadlock
            (run['pending_row_ids'], config['list_id'], run['user_id']),
        ).fetchall()
        # Read after the lock: a lock wait re-reads the row, not row_charges, so a turn that just committed is seen.
        rows = conn.execute(
            f"SELECT r.id,r.cells,{PENDING_KEYS_SQL} <> '{{}}' AS held FROM list_rows r WHERE r.id=ANY(%s::uuid[])",
            ([r['id'] for r in locked],),
        ).fetchall()
        context = enrollment_context(conn, campaign)
        pending, targets = [], []
        for row in rows:
            if row['held']:  # #462: its turn may still refund cells; maintain() retries it once the turn settles
                pending.append(str(row['id']))
                continue
            cells = row['cells']
            verdict, email, linkedin = enrollment_verdict(cells, campaign, context)
            if verdict == 'enroll' and (reason := holds.for_campaign(conn, campaign, email, linkedin)):
                verdict = 'hold' if reason == 'held_by_instantly' else 'skip'
            if verdict == 'skip':
                continue
            if verdict == 'hold':
                pending.append(str(row['id']))
                continue
            if room <= 0:  # the campaign filled mid-run: this person stays in the list only
                continue
            inserted = insert_draft(conn, campaign, run['user_id'], cells, email, row['id'], 'refill')
            if inserted:
                room -= 1
                targets.append(inserted)
        freeze(conn, campaign, targets)
        # Retain the held rows (another campaign's person, a pending charge). Insertions and the marker commit together.
        conn.execute(
            'UPDATE saved_search_runs SET refill_done=%s,pending_row_ids=%s::uuid[] WHERE id=%s',
            (not pending, pending, run_id),
        )


def maintain():
    """Called in the beat's System scope, which only selects: a dead run, or a queued one whose owner deleted their
    account (#333: nobody to run it as), is closed as its organization; a refill runs as the run's owner. A refill
    whose owner is no longer a live member waits, like their searches."""
    with get_conn() as conn:
        stale = conn.execute(
            "SELECT id, organization_id FROM saved_search_runs WHERE status IN ('queued', 'running')"
            " AND (started_at < %s OR (status = 'queued' AND user_id IS NULL))",
            (datetime.now(UTC) - STALE_AFTER,),
        ).fetchall()
        pending = conn.execute(
            'SELECT r.id, r.organization_id, r.user_id FROM saved_search_runs r'
            ' JOIN saved_searches s ON s.id=r.saved_search_id'
            " JOIN outreach_campaigns c ON c.id=(r.config->>'campaign_id')::uuid AND c.owner_id IS NOT DISTINCT FROM r.user_id"
            " WHERE r.status='succeeded' AND NOT r.refill_done AND s.enabled AND s.deleted_at IS NULL"
            " AND c.status IN ('active','stopped','completed')"
            ' AND (r.user_id, r.organization_id) IN (SELECT user_id, organization_id FROM zb.live_members)'
            ' ORDER BY r.refill_checked_at NULLS FIRST,r.finished_at LIMIT 100'
        ).fetchall()
    for row in stale:
        with acting_for(str(row['organization_id']), 'dead saved search run'):
            _reap(row['id'])
    for row in pending:
        try:
            tasks.for_member(str(row['organization_id']), str(row['user_id']), partial(refill, row['id']))
        except Exception as exc:
            logger.error('saved_search.refill_failed', run_id=str(row['id']), error_type=type(exc).__name__)


def _reap(run_id) -> None:
    with get_conn() as conn:
        _, run = locked_run(conn, run_id)
        if run and run['status'] == 'queued' and run['user_id'] is None:
            stop(conn, run, 'cancelled', 'creator_deleted')
            return
        if not (run and run['status'] == 'running' and run['started_at'] < datetime.now(UTC) - STALE_AFTER):
            return
        stop(conn, run, 'unknown', 'worker_outcome_unknown')
        if not run['reservation_id']:
            return
        org = wallet(conn, run)
    balance.changed(org)  # the hold, released
