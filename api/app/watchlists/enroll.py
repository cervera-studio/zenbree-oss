"""A signal adds the account's people to a campaign (E13.3b, #648).

When the live score of an account's unconsumed signals reaches the watchlist's threshold, the people of that account
that already sit in a person list of the project enter the campaign as draft people, by the one enrollment rule
(``enroll_rows``: exclusions, duplicates, prior contacts). No search and no credits. Each signal is consumed
(``enrolled_at``) in the transaction that inserts the people, so a retry or a later run never enters anybody twice for
it. An account with no known person, or whose people were all left out for want of room or because they are in
another campaign, keeps its signals and is counted, so a later run tries again.
"""

from __future__ import annotations

from collections import defaultdict

import structlog

from app.billing.row_charges import finalize
from app.core.db import get_conn
from app.lists.people_match import PeopleIndex
from app.lists.rows import DELIVERED_SQL
from app.outreach.drafts import MAX_TARGETS, enroll_rows, enrolled_status
from app.scheduling.runner import enrollment_context

logger = structlog.get_logger()

_SIGNALS_SQL = """
SELECT s.id, s.row_id, signal_score(s.score, s.occurred_at)::float8 AS score
FROM signals s WHERE s.watchlist_id = %s AND s.enrolled_at IS NULL
ORDER BY s.row_id, score DESC"""

# The people of the project's person lists, as the company's people_count matches them (app.lists.people_match).
_PEOPLE_SQL = f"""
SELECT r.id, r.cells, r.cells ->> 'company_domain' AS domain, r.cells ->> 'company_name' AS company_name
FROM list_rows r JOIN lists l ON l.id = r.list_id
WHERE l.project_id = %s AND l.entity = 'person' AND l.deleted_at IS NULL AND {DELIVERED_SQL}
ORDER BY r.relevance_score DESC NULLS LAST, r.seq"""

# Left out for a reason that may pass: the next run tries again while the signal stays.
_RETRY = ('over_limit', 'in_other_campaign', 'held_by_instantly')


def _ready(conn, watchlist) -> dict[str, list[dict]]:
    """The account's unconsumed signals, strongest first, for the accounts whose summed score reaches the threshold."""
    by_account: defaultdict[str, list[dict]] = defaultdict(list)
    for signal in conn.execute(_SIGNALS_SQL, (watchlist['id'],)).fetchall():
        by_account[str(signal['row_id'])].append(signal)
    return {a: s for a, s in by_account.items() if sum(x['score'] for x in s) >= watchlist['campaign_threshold']}


def _known_people(watchlist) -> dict[str, list[dict]]:
    """The people of each ready account. Read before any lock: the matching walks the project's people, and the
    project lock stalls every enrollment of the project."""
    with get_conn() as conn:
        ready = _ready(conn, watchlist)
        if not ready:
            return {}
        accounts = conn.execute(
            'SELECT id, cells FROM list_rows WHERE id = ANY(%s::uuid[]) AND NOT excluded', (list(ready),)
        ).fetchall()
        index = PeopleIndex()
        for person in conn.execute(_PEOPLE_SQL, (watchlist['project_id'],)).fetchall():
            index.add(person, domain=person['domain'], company_name=person['company_name'])
    return {str(a['id']): index.at(a['cells']) for a in accounts}


def enroll_signals(run_id) -> None:
    """Enroll for the run's watchlist, in the scope of the run. Nothing when it names no campaign, or the campaign
    ended."""
    with get_conn() as conn:
        watchlist = conn.execute(
            'SELECT w.id, w.campaign_id, w.campaign_threshold, l.project_id FROM watchlist_runs r'
            ' JOIN watchlists w ON w.id = r.watchlist_id JOIN lists l ON l.id = w.list_id WHERE r.id = %s',
            (run_id,),
        ).fetchone()
    if not watchlist or not watchlist['campaign_id']:
        return
    known = _known_people(watchlist)
    if not known:
        return
    with get_conn() as conn:
        # Same lock order as enrollment and refill: the project first.
        conn.execute('SELECT id FROM projects WHERE id = %s FOR NO KEY UPDATE', (watchlist['project_id'],))
        campaign = conn.execute(
            "SELECT * FROM outreach_campaigns WHERE id = %s AND status NOT IN ('stopped', 'completed') FOR UPDATE",
            (watchlist['campaign_id'],),
        ).fetchone()
        if not campaign:
            return
        # The link as it is now, locked: an update that cleared or moved it first stops this run, one that comes
        # later waits for the commit. Campaign before watchlist, as the foreign key's SET NULL and ``update`` take
        # them. FOR SHARE, not FOR UPDATE: it only has to keep ``update`` out, and the run changes nothing here.
        current = conn.execute(
            'SELECT campaign_id, campaign_threshold FROM watchlists WHERE id = %s FOR SHARE', (watchlist['id'],)
        ).fetchone()
        if not current or current['campaign_id'] != campaign['id']:
            return
        watchlist = {**watchlist, 'campaign_threshold': current['campaign_threshold']}
        conn.execute(
            'SELECT id FROM signals WHERE watchlist_id = %s AND enrolled_at IS NULL FOR UPDATE', (watchlist['id'],)
        )
        ready = _ready(conn, watchlist)  # again, locked: a run that finished first has consumed its signals
        context = enrollment_context(conn, campaign)
        have = conn.execute(
            'SELECT count(*) AS n FROM outreach_campaign_targets WHERE campaign_id = %s', (campaign['id'],)
        ).fetchone()['n']
        enrolled_total, without, left_out = 0, 0, 0
        for account_id, signals in ready.items():
            if account_id not in known:  # its signals reached the threshold after the read: the next run
                continue
            if not known[account_id]:
                without += 1
                continue
            # The strongest signal is the reason the people entered.
            enrolled, why = enroll_rows(
                conn,
                campaign,
                campaign['owner_id'],
                known[account_id],
                context,
                room=MAX_TARGETS - have - enrolled_total,
                source='signal',
                signal_id=signals[0]['id'],
                status=enrolled_status(campaign),
            )
            finalize(conn, enrolled)
            enrolled_total += len(enrolled)
            left_out += sum(why.values())
            if not enrolled and any(why[reason] for reason in _RETRY):
                continue
            conn.execute(
                'UPDATE signals SET enrolled_at = now() WHERE id = ANY(%s::uuid[])', ([s['id'] for s in signals],)
            )
        conn.execute(
            'UPDATE watchlist_runs SET people_enrolled = %s, accounts_without_people = %s, people_left_out = %s'
            ' WHERE id = %s',
            (enrolled_total, without, left_out, run_id),
        )
    if enrolled_total:
        logger.info('watchlist.enrolled', run_id=str(run_id), people=enrolled_total)
