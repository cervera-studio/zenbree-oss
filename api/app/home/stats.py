"""Home dashboard numbers (#826): 30-day KPIs against the 30 before, the last 8 ISO weeks, the funnel and the
latest events of the active project. One query per section; the web computes the deltas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.home.overview import FINISHED_SEARCH_SQL
from app.outreach.inbound import HIGH_INTENT

WEEKS = 8
EVENTS = 10

ActivityKind = Literal['search_finished', 'campaign_started', 'reply_received']


class Kpi(BaseModel):
    value: int
    previous: int


class Kpis(BaseModel):
    leads: Kpi
    contacts: Kpi
    messages_sent: Kpi
    replies: Kpi
    positive_replies: Kpi


class Week(BaseModel):
    week: datetime
    messages_sent: int
    replies: int


class Funnel(BaseModel):
    leads: int
    contacts: int
    contacted: int
    replied: int
    positive: int


class HomeEvent(BaseModel):
    kind: ActivityKind
    at: datetime
    title: str | None
    link: str | None


class Stats(BaseModel):
    kpis: Kpis
    weeks: list[Week]
    funnel: Funnel
    activity: list[HomeEvent]


_HIGH = sorted(HIGH_INTENT)

_ROWS_SQL = """
SELECT count(*) FILTER (WHERE r.created_at >= now() - interval '30 days') AS leads,
       count(*) FILTER (WHERE r.created_at >= now() - interval '60 days' AND r.created_at < now() - interval '30 days')
           AS leads_prev,
       count(*) FILTER (WHERE r.created_at >= now() - interval '30 days' AND has.contact) AS contacts,
       count(*) FILTER (WHERE r.created_at >= now() - interval '60 days' AND r.created_at < now() - interval '30 days'
                          AND has.contact) AS contacts_prev
  FROM lists l JOIN list_rows r ON r.list_id = l.id
 CROSS JOIN LATERAL (SELECT coalesce(r.cells->>'email', '') <> '' OR coalesce(r.cells->>'phone', '') <> '' AS contact)
       has
 WHERE l.organization_id = %(org)s AND l.project_id = %(project)s AND l.deleted_at IS NULL AND NOT r.excluded
   AND r.created_at >= now() - interval '60 days'"""

_SENT_SQL = """
SELECT count(*) FILTER (WHERE s.delivered_at >= now() - interval '30 days') AS sent,
       count(*) FILTER (WHERE s.delivered_at < now() - interval '30 days') AS sent_prev,
       count(DISTINCT s.recipient) FILTER (WHERE s.delivered_at >= now() - interval '30 days') AS contacted
  FROM outreach_campaigns c JOIN outreach_campaign_sends s ON s.campaign_id = c.id
 WHERE c.organization_id = %(org)s AND c.project_id = %(project)s AND s.status = 'sent'
   AND s.delivered_at >= now() - interval '60 days'"""

_REPLIES_SQL = """
SELECT count(*) FILTER (WHERE received_at >= now() - interval '30 days') AS replies,
       count(*) FILTER (WHERE received_at < now() - interval '30 days') AS replies_prev,
       count(*) FILTER (WHERE received_at >= now() - interval '30 days' AND label = ANY(%(high)s)) AS positive,
       count(*) FILTER (WHERE received_at < now() - interval '30 days' AND label = ANY(%(high)s)) AS positive_prev,
       count(DISTINCT person) FILTER (WHERE received_at >= now() - interval '30 days') AS people,
       count(DISTINCT person) FILTER (WHERE received_at >= now() - interval '30 days' AND label = ANY(%(high)s))
           AS people_positive
  FROM outreach_inbound_messages
 CROSS JOIN LATERAL (SELECT coalesce(nullif(lower(from_address), ''), target_id::text, id::text) AS person) p
 WHERE organization_id = %(org)s AND project_id = %(project)s AND kind = 'reply'
   AND received_at >= now() - interval '60 days'"""

_WEEKS_SQL = """
SELECT w.week, coalesce(s.n, 0) AS messages_sent, coalesce(m.n, 0) AS replies
  FROM generate_series(date_trunc('week', now()) - make_interval(weeks => %(weeks)s - 1),
                       date_trunc('week', now()), interval '1 week') AS w(week)
  LEFT JOIN (SELECT date_trunc('week', s.delivered_at) AS week, count(*) AS n
               FROM outreach_campaigns c JOIN outreach_campaign_sends s ON s.campaign_id = c.id
              WHERE c.organization_id = %(org)s AND c.project_id = %(project)s AND s.status = 'sent'
                AND s.delivered_at >= date_trunc('week', now()) - make_interval(weeks => %(weeks)s - 1)
              GROUP BY 1) s ON s.week = w.week
  LEFT JOIN (SELECT date_trunc('week', received_at) AS week, count(*) AS n
               FROM outreach_inbound_messages
              WHERE organization_id = %(org)s AND project_id = %(project)s AND kind = 'reply'
                AND received_at >= date_trunc('week', now()) - make_interval(weeks => %(weeks)s - 1)
              GROUP BY 1) m ON m.week = w.week
 ORDER BY w.week"""

# No jobs index leads on finished_at, so the organization's succeeded jobs are sorted, and a filter beside the sort
# would run on all of them. The OFFSET 0 fences sort only ids, then check one job at a time (its project, list and
# message, whose tool_calls hold tens of KB), so the limit stops at the tenth search.
_ACTIVITY_SQL = f"""
SELECT kind, at, title, link FROM (
  (SELECT 'search_finished' AS kind, c.finished_at AS at, j.title, '/lists/' || j.list_id AS link
     FROM (SELECT j.id, j.finished_at FROM jobs j
            WHERE j.organization_id = %(org)s AND j.status = 'succeeded' AND j.finished_at IS NOT NULL
            ORDER BY j.finished_at DESC OFFSET 0) c
    CROSS JOIN LATERAL (SELECT j.status, j.stages, j.result, j.list_id, s.title
                          FROM jobs j JOIN chat_sessions s ON s.id = j.chat_session_id
                          JOIN lists l ON l.id = j.list_id AND l.deleted_at IS NULL
                         WHERE j.id = c.id AND s.organization_id = %(org)s AND s.project_id = %(project)s OFFSET 0) j
    WHERE {FINISHED_SEARCH_SQL}
    ORDER BY c.finished_at DESC LIMIT %(n)s)
  UNION ALL
  (SELECT 'campaign_started', c.activated_at, c.name, '/campaigns/' || c.id
     FROM outreach_campaigns c
    WHERE c.organization_id = %(org)s AND c.project_id = %(project)s AND c.activated_at IS NOT NULL
    ORDER BY c.activated_at DESC LIMIT %(n)s)
  UNION ALL
  (SELECT 'reply_received', m.received_at, nullif(btrim(coalesce(nullif(m.from_name, ''), m.from_address)), ''),
          '/inbox'
     FROM outreach_inbound_messages m
    WHERE m.organization_id = %(org)s AND m.project_id = %(project)s AND m.kind = 'reply'
    ORDER BY m.received_at DESC LIMIT %(n)s)
) e ORDER BY at DESC LIMIT %(n)s"""


def load_stats(conn, org_id: str, project_id: str) -> Stats:
    p = {'org': org_id, 'project': project_id, 'high': _HIGH, 'weeks': WEEKS, 'n': EVENTS}
    rows = conn.execute(_ROWS_SQL, p).fetchone()
    sent = conn.execute(_SENT_SQL, p).fetchone()
    replies = conn.execute(_REPLIES_SQL, p).fetchone()
    weeks = conn.execute(_WEEKS_SQL, p).fetchall()
    activity = conn.execute(_ACTIVITY_SQL, p).fetchall()
    return Stats(
        kpis=Kpis(
            leads=Kpi(value=rows['leads'], previous=rows['leads_prev']),
            contacts=Kpi(value=rows['contacts'], previous=rows['contacts_prev']),
            messages_sent=Kpi(value=sent['sent'], previous=sent['sent_prev']),
            replies=Kpi(value=replies['replies'], previous=replies['replies_prev']),
            positive_replies=Kpi(value=replies['positive'], previous=replies['positive_prev']),
        ),
        weeks=[Week(**w) for w in weeks],
        funnel=Funnel(
            leads=rows['leads'],
            contacts=rows['contacts'],
            contacted=sent['contacted'],
            replied=replies['people'],
            positive=replies['people_positive'],
        ),
        activity=[HomeEvent(**a) for a in activity],
    )
