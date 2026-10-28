"""How many people each Instantly campaign of a project reached in a time range (#942): the counts of the campaign
report, by its rule: a person counts once per measure, on the date of their first fact of that kind in the campaign.

A person is an address within (workspace, campaign). Counting people, not rows, is what keeps the webhook copy and
the API copy of one mail from counting twice; sends are rows, so a webhook send within ``MATCH_WINDOW`` of a send the
API listed to the same address, of the same campaign and step (or with the step unknown on either side), is left out.
A reply counts as human as ``fold`` reads it: a human or opt-out vote, or no vote once the key may not read emails.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from app.integrations.instantly_sync.vocabulary import MATCH_WINDOW, Label

POSITIVE = (Label.INTERESTED, Label.MEETING_BOOKED, Label.MEETING_COMPLETED, Label.WON)

# The measure a fact counts for, over the alias given to ``format``. ``reply_nature`` and ``our_class`` hold the two
# votes (``vocabulary.reply_class``): either is enough to make a reply human or an opt-out, as the safer vote wins.
_MEASURE = """CASE
    WHEN {0}.kind = 'sent' THEN 'contacted'
    WHEN {0}.kind = 'reply' AND ({0}.reply_nature = 'human' OR {0}.our_class = 'opt_out'
         OR (%(refused)s AND {0}.reply_nature = 'unclassified' AND {0}.our_class IS NULL)) THEN 'human_replies'
    WHEN {0}.kind = 'interest' AND {0}.label = ANY(%(positive)s) THEN 'positive_replies'
    WHEN {0}.kind = 'bounced' THEN 'bounced'
END"""

_FIRSTS = f"""
SELECT DISTINCT ON (f.workspace_id, f.campaign_id, f.email, f.measure) f.*
  FROM (SELECT e.id, e.lead_id, e.project_id, e.workspace_id, e.campaign_id, e.email, e.occurred_at,
               {_MEASURE.format('e')} AS measure
          FROM instantly_events e
         WHERE e.project_id = %(project)s AND e.occurred_at >= %(start)s AND e.occurred_at < %(end)s) f
 WHERE f.measure IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM instantly_events p
                    WHERE p.project_id = f.project_id AND p.email = f.email AND p.workspace_id = f.workspace_id
                      AND p.campaign_id = f.campaign_id AND p.occurred_at < %(start)s
                      AND {_MEASURE.format('p')} = f.measure)
 ORDER BY f.workspace_id, f.campaign_id, f.email, f.measure, f.occurred_at, f.id"""

# A person's list is the one their lead came from: the row's list, else the enrollment's.
_LINEAGE = f"""
SELECT f.workspace_id, f.campaign_id, li.id::text AS list_id, li.name AS list_name,
       count(*) FILTER (WHERE f.measure = 'contacted') AS contacted,
       count(*) FILTER (WHERE f.measure = 'human_replies') AS human_replies,
       count(*) FILTER (WHERE f.measure = 'positive_replies') AS positive_replies,
       count(*) FILTER (WHERE f.measure = 'bounced') AS bounced
  FROM ({_FIRSTS}) f
  JOIN instantly_leads l ON l.id = f.lead_id
  JOIN instantly_enrollments b ON b.id = l.enrollment_id
  LEFT JOIN list_rows r ON r.id = l.row_id
  LEFT JOIN lists li ON li.id = coalesce(r.list_id, b.list_id) AND li.deleted_at IS NULL
 GROUP BY 1, 2, 3, 4
 ORDER BY 1, 2, 4 NULLS LAST, 3"""

_CAMPAIGNS = """
SELECT b.workspace_id, b.campaign_id, b.name, coalesce(l.enrolled, 0) AS enrolled, coalesce(e.sent, 0) AS sent
  FROM (SELECT workspace_id, campaign_id, (array_agg(campaign_name ORDER BY created_at DESC))[1] AS name
          FROM instantly_enrollments WHERE project_id = %(project)s GROUP BY 1, 2) b
  LEFT JOIN (SELECT workspace_id, campaign_id, count(*) AS enrolled FROM instantly_leads
              WHERE project_id = %(project)s AND outcome = 'accepted' GROUP BY 1, 2) l USING (workspace_id, campaign_id)
  LEFT JOIN (SELECT e.workspace_id, e.campaign_id, count(*) AS sent FROM instantly_events e
              WHERE e.project_id = %(project)s AND e.kind = 'sent' AND e.occurred_at >= %(start)s AND e.occurred_at < %(end)s
                AND NOT (e.provider_ref IS NULL AND EXISTS (
                    SELECT 1 FROM instantly_events o
                     WHERE o.project_id = e.project_id AND o.email = e.email AND o.workspace_id = e.workspace_id
                       AND o.campaign_id = e.campaign_id AND o.kind = 'sent' AND o.provider_ref IS NOT NULL
                       AND (e.step IS NULL OR o.step IS NULL OR o.step = e.step)
                       AND o.occurred_at BETWEEN e.occurred_at - %(match)s AND e.occurred_at + %(match)s))
              GROUP BY 1, 2) e USING (workspace_id, campaign_id)
 ORDER BY 3, 2"""

_MEASURES = ('contacted', 'human_replies', 'positive_replies')


def campaigns(conn, project_id: str, start: datetime, end: datetime, *, emails_refused: bool) -> list[dict]:
    """Each campaign the project enrolled people into, by name, as ``CampaignReportRow`` values."""
    params = {
        'project': project_id,
        'start': start,
        'end': end,
        'match': MATCH_WINDOW,
        'positive': [str(label) for label in POSITIVE],
        'refused': emails_refused,
    }
    lineage: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in conn.execute(_LINEAGE, params):
        lineage[r['workspace_id'], r['campaign_id']].append(r)
    return [_row(c, lineage[c['workspace_id'], c['campaign_id']]) for c in conn.execute(_CAMPAIGNS, params)]


def _row(c: dict, lineage: list[dict]) -> dict:
    return {
        'sender': 'instantly',
        'id': c['campaign_id'],
        'name': c['name'],
        'status': None,
        'list': None,
        'counts': {
            'enrolled': c['enrolled'],
            **{m: sum(r[m] for r in lineage) for m in (*_MEASURES, 'bounced')},
            'sent': {'email': c['sent'], 'linkedin_connect': 0, 'linkedin_message': 0},
            'meetings': None,
            'deals': None,
        },
        'lineage': [
            {
                'list': {'id': r['list_id'], 'name': r['list_name']} if r['list_id'] else None,
                'source': None,
                'signal': None,
                **{m: r[m] for m in _MEASURES},
            }
            for r in lineage
        ],
        'variants': [],  # Zenbree stores no Instantly A/B variant (#934)
    }
