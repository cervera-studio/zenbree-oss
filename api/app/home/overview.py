from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal, NamedTuple

from pydantic import BaseModel

from app.agent.jobs import STALE_JOB_MINUTES, failed_step_sql
from app.lists.lookups import verified_email_sql
from app.notifications.service import unread_replies
from app.outreach import approval_queue
from app.outreach.inbound import HIGH_INTENT
from app.outreach.routes import project_campaigns

APPROVAL_CAMPAIGNS = 2
RECENT_LISTS = 5
RECENT_CAMPAIGNS = 3
LIVE_CAMPAIGN = ['active', 'paused']
FINISHED_MINUTES = 2

_VERIFIED_SQL = f'NOT r.excluded AND {verified_email_sql("r.cells")}'

# A search the Home shows as finished, for a job `j`: it succeeded with no failed step, and its list has a row. A turn
# whose searches all failed still succeeds, on the empty list its plan created.
FINISHED_SEARCH_SQL = (
    f"j.status = 'succeeded' AND NOT {failed_step_sql()}"
    ' AND EXISTS (SELECT 1 FROM list_rows r WHERE r.list_id = j.list_id)'
)


class Person(BaseModel):
    id: str
    name: str


class ApprovalCampaign(BaseModel):
    id: str
    name: str
    count: int


class Approvals(BaseModel):
    total: int
    campaigns: list[ApprovalCampaign]


class LatestReply(BaseModel):
    name: str
    campaign: str | None
    intent: Literal['interested', 'meeting_request'] | None
    at: datetime


class Replies(BaseModel):
    count: int
    latest: LatestReply | None


class RunningSearch(BaseModel):
    job_id: str
    session_id: str
    list_id: str | None
    new_list: bool
    title: str | None
    stage_tool: str | None
    entity: Literal['company', 'person'] | None
    stages_done: int
    stages_total: int
    progress_done: int
    progress_total: int
    rows: int
    started_by: Person | None
    started_at: datetime


class FinishedSearch(BaseModel):
    job_id: str
    session_id: str
    title: str | None
    entity: Literal['company', 'person']
    list_id: str
    new_list: bool
    rows: int
    verified_emails: int | None
    stages_total: int


class RecentList(BaseModel):
    id: str
    name: str
    entity: Literal['company', 'person']
    mode: Literal['build', 'enrich']
    rows: int
    verified_emails: int | None
    updated_at: datetime
    owner: Person | None


class ActiveCampaign(BaseModel):
    id: str
    name: str
    status: Literal['active', 'paused']
    channels: list[Literal['email', 'linkedin', 'hubspot']]
    sent: int
    replies: int
    owner: Person | None


class CampaignHint(BaseModel):
    list_id: str
    name: str
    verified_emails: int


class Overview(BaseModel):
    approvals: Approvals
    replies: Replies
    running: list[RunningSearch]
    finished: list[FinishedSearch]
    lists: list[RecentList]
    campaigns: list[ActiveCampaign]
    campaign_hint: CampaignHint | None


class Snapshot(NamedTuple):
    overview: Overview
    refresh_at: datetime | None


def awaiting_approval(conn, org_id: str, project_id: str) -> list[dict]:
    waiting = {cid: n.awaiting for cid, n in approval_queue.counts(conn, project_id).items() if n.awaiting}
    rows = conn.execute(
        'SELECT id, name FROM outreach_campaigns WHERE organization_id = %s AND project_id = %s AND id = ANY(%s)'
        ' ORDER BY created_at DESC',
        (org_id, project_id, list(waiting)),
    ).fetchall()
    return sorted(({**r, 'n': waiting[r['id']]} for r in rows), key=lambda r: -r['n'])


_JOB_COLS = f"""
SELECT j.id::text AS job_id, j.chat_session_id AS session_id, s.title, j.stages, j.progress_done, j.progress_total,
       j.user_id, j.created_at AS started_at, j.updated_at, j.finished_at, l.id::text AS list_id,
       coalesce(l.created_at >= j.created_at, false) AS new_list, l.entity, coalesce(rs.rows, 0) AS rows,
       CASE WHEN l.entity = 'person' THEN coalesce(rs.verified, 0) END AS verified_emails
  FROM jobs j JOIN chat_sessions s ON s.id = j.chat_session_id
  LEFT JOIN lists l ON l.id = j.list_id AND l.deleted_at IS NULL
  LEFT JOIN LATERAL (
       SELECT count(*) FILTER (WHERE NOT r.excluded) AS rows, count(*) FILTER (WHERE {_VERIFIED_SQL}) AS verified
         FROM list_rows r WHERE r.list_id = l.id) rs ON true
 WHERE s.project_id = %s"""


def running_jobs(conn, project_id: str, user_id: str | None = None) -> list[dict]:
    return conn.execute(
        _JOB_COLS + " AND j.status IN ('queued', 'running')"
        f" AND j.updated_at >= now() - interval '{STALE_JOB_MINUTES} minutes'"
        ' AND (%s::uuid IS NULL OR j.user_id = %s) ORDER BY j.created_at DESC',
        (project_id, user_id, user_id),
    ).fetchall()


def finished_jobs(conn, project_id: str) -> list[dict]:
    return conn.execute(
        _JOB_COLS + f' AND l.id IS NOT NULL AND {FINISHED_SEARCH_SQL}'
        f" AND j.finished_at >= now() - interval '{FINISHED_MINUTES} minutes' ORDER BY j.finished_at DESC",
        (project_id,),
    ).fetchall()


def _names(conn, ids: set) -> dict[str, Person]:
    rows = conn.execute('SELECT id::text, name FROM "user" WHERE id = ANY(%s)', (list(ids),)).fetchall() if ids else []
    return {r['id']: Person(id=r['id'], name=r['name']) for r in rows}


def _channel(step_channel: str) -> Literal['email', 'linkedin', 'hubspot']:
    if step_channel == 'hubspot_task':
        return 'hubspot'
    return 'email' if step_channel == 'email' else 'linkedin'


def _running(row: dict, people: dict[str, Person]) -> RunningSearch:
    stages = row['stages'] or []
    open_stage = next((s for s in stages if s['done'] < s['units']), None)
    return RunningSearch(
        job_id=row['job_id'],
        session_id=str(row['session_id']),
        list_id=row['list_id'],
        new_list=row['new_list'],
        title=row['title'],
        stage_tool=open_stage['tool'] if open_stage else None,
        entity=row['entity'],
        stages_done=sum(s['done'] >= s['units'] for s in stages),
        stages_total=len(stages),
        progress_done=row['progress_done'],
        progress_total=row['progress_total'],
        rows=row['rows'],
        started_by=people.get(str(row['user_id'])),
        started_at=row['started_at'],
    )


def _finished(row: dict) -> FinishedSearch:
    return FinishedSearch(
        job_id=row['job_id'],
        session_id=str(row['session_id']),
        title=row['title'],
        entity=row['entity'],
        list_id=row['list_id'],
        new_list=row['new_list'],
        rows=row['rows'],
        verified_emails=row['verified_emails'],
        stages_total=len(row['stages'] or []),
    )


def _refresh_at(running: list[dict], finished: list[dict]) -> datetime | None:
    return min(
        [j['updated_at'] + timedelta(minutes=STALE_JOB_MINUTES) for j in running]
        + [j['finished_at'] + timedelta(minutes=FINISHED_MINUTES) for j in finished],
        default=None,
    )


_LISTS_SQL = f"""
SELECT l.id::text, l.name, l.entity, l.mode, l.user_id::text AS owner_id, rs.rows,
       greatest(l.created_at, rs.last_row) AS updated_at,
       CASE WHEN l.entity = 'person' THEN rs.verified END AS verified_emails
  FROM lists l
 CROSS JOIN LATERAL (
       SELECT count(*) FILTER (WHERE NOT r.excluded) AS rows, max(r.created_at) AS last_row,
              count(*) FILTER (WHERE {_VERIFIED_SQL}) AS verified
         FROM list_rows r WHERE r.list_id = l.id) rs
 WHERE l.organization_id = %s AND l.project_id = %s AND l.deleted_at IS NULL
 ORDER BY updated_at DESC LIMIT %s"""

_HINT_SQL = f"""
SELECT l.id::text AS list_id, l.name, v.n AS verified_emails
  FROM lists l
 CROSS JOIN LATERAL (SELECT count(*) AS n FROM list_rows r WHERE r.list_id = l.id AND {_VERIFIED_SQL}) v
 WHERE l.organization_id = %s AND l.project_id = %s AND l.entity = 'person' AND v.n > 0
   AND l.deleted_at IS NULL
   AND NOT EXISTS (SELECT 1 FROM outreach_campaigns c WHERE c.list_id = l.id)
 ORDER BY l.created_at DESC LIMIT 1"""


def load_overview(conn, user_id: str, org_id: str, project_id: str) -> Snapshot:
    waiting = awaiting_approval(conn, org_id, project_id)
    count, latest = unread_replies(conn, user_id, org_id)
    jobs = running_jobs(conn, project_id)
    done = finished_jobs(conn, project_id)
    # ponytail: a list's activity is read off all its rows; fine until lists are large, then keep an updated_at.
    lists = conn.execute(_LISTS_SQL, (org_id, project_id, RECENT_LISTS)).fetchall()
    camps = project_campaigns(conn, org_id, project_id, statuses=LIVE_CAMPAIGN, limit=RECENT_CAMPAIGNS)
    people = _names(
        conn,
        {str(j['user_id']) for j in jobs if j['user_id']}
        | {r['owner_id'] for r in lists if r['owner_id']}
        | {str(c['owner_id']) for c in camps if c['owner_id']},
    )
    hint = None if camps else conn.execute(_HINT_SQL, (org_id, project_id)).fetchone()
    overview = Overview(
        approvals=Approvals(
            total=sum(w['n'] for w in waiting),
            campaigns=[
                ApprovalCampaign(id=str(w['id']), name=w['name'], count=w['n']) for w in waiting[:APPROVAL_CAMPAIGNS]
            ],
        ),
        replies=Replies(
            count=count,
            latest=LatestReply(
                name=str(latest.get('name') or '').strip(),
                campaign=latest.get('campaign'),
                intent=latest['label'] if latest.get('label') in HIGH_INTENT and not latest.get('unverified') else None,
                at=latest['at'],
            )
            if latest
            else None,
        ),
        running=[_running(j, people) for j in jobs],
        finished=[_finished(j) for j in done],
        lists=[RecentList(**{**r, 'owner': people.get(r['owner_id'])}) for r in lists],
        campaigns=[
            ActiveCampaign(
                id=str(c['id']),
                name=c['name'],
                status=c['status'],
                channels=list(dict.fromkeys(_channel(ch) for ch in c['step_channels'])),
                sent=c['sent'],
                replies=c['replied'],
                owner=people.get(str(c['owner_id'])),
            )
            for c in camps
        ],
        campaign_hint=CampaignHint(**hint) if hint else None,
    )
    return Snapshot(overview, _refresh_at(jobs, done))
