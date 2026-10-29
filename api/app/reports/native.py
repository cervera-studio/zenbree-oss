"""The native campaigns of a project in a report window (#942), by the counting rule of 2026-10-17 in
``docs/decisions.md``: a person counts once per measure, on the date of their first event of that kind in the
campaign.

An event with a target is a first when the campaign holds no earlier one of that target, so its cost follows the
window and the person's own rows, not the campaign's history. That probe sits in a CASE so that it stays a subplan,
run once per event by the target, never a join over every row of both kinds.

An event with no target is a person known only by address: a send whose target was deleted is the address it went
to, and a reply with no target is the address it came from (a person deleted since, or a sender who matched no
person, such as a colleague who answered). A reply with no target and no address (a LinkedIn reply with no profile
URL) is a person of its own. These take one grouped pass over the campaign's events with no target: under row-level
security lower() and btrim() are not leakproof, so the address can never be an index condition, and a probe per
event would read the campaign's whole unmatched history once per event."""

from __future__ import annotations

from collections import defaultdict

from app.outreach.inbound import HIGH_INTENT
from app.reports import variant_split
from app.reports.window import Window

# A CRM task is work for the user, not a message to the person: it neither contacts nor counts as a send.
_DELIVERED = "{0}.status = 'sent' AND {0}.channel <> 'hubspot_task'"
_REPLY = "{0}.kind = 'reply'"
_POSITIVE = "{0}.kind = 'reply' AND {0}.label = ANY(%(positive)s)"
_ENROLLED = "{0}.status IN ('active', 'paused', 'replied')"
_TESTS = "jsonb_path_exists({0}.steps, '$[*].b ? (@ != null)')"


def _sends(measure: str) -> str:
    # The CASE has no ELSE: a send with no target is null here and counts in the grouped pass.
    return f"""
    SELECT DISTINCT '{measure}' AS measure, s.campaign_id, s.target_id, s.target_id::text AS person
      FROM campaigns c JOIN outreach_campaign_sends s ON s.campaign_id = c.id
     WHERE {_DELIVERED.format('s')} AND s.delivered_at >= %(start)s AND s.delivered_at < %(end)s
       AND CASE WHEN s.target_id IS NOT NULL
                THEN NOT EXISTS (SELECT 1 FROM outreach_campaign_sends p
                                  WHERE p.campaign_id = s.campaign_id AND p.target_id = s.target_id
                                    AND {_DELIVERED.format('p')} AND p.delivered_at < %(start)s)
           END
    UNION ALL
    SELECT '{measure}', s.campaign_id, NULL, lower(btrim(s.recipient))
      FROM campaigns c JOIN outreach_campaign_sends s ON s.campaign_id = c.id
     WHERE {_DELIVERED.format('s')} AND s.target_id IS NULL AND s.delivered_at < %(end)s
     GROUP BY s.campaign_id, lower(btrim(s.recipient))
    HAVING min(s.delivered_at) >= %(start)s"""


def _replies(measure: str, kind: str) -> str:
    return f"""
    SELECT DISTINCT '{measure}' AS measure, m.campaign_id, m.target_id, m.target_id::text AS person
      FROM campaigns c JOIN outreach_inbound_messages m ON m.campaign_id = c.id
     WHERE {kind.format('m')} AND m.received_at >= %(start)s AND m.received_at < %(end)s
       AND CASE WHEN m.target_id IS NOT NULL
                THEN NOT EXISTS (SELECT 1 FROM outreach_inbound_messages p
                                  WHERE p.target_id = m.target_id AND p.campaign_id = m.campaign_id
                                    AND {kind.format('p')} AND p.received_at < %(start)s)
           END
    UNION ALL
    SELECT '{measure}', m.campaign_id, NULL, coalesce(nullif(lower(btrim(m.from_address)), ''), m.sender_provider_id, m.id::text)
      FROM campaigns c JOIN outreach_inbound_messages m ON m.campaign_id = c.id
     WHERE {kind.format('m')} AND m.target_id IS NULL AND m.received_at < %(end)s
     GROUP BY m.campaign_id, coalesce(nullif(lower(btrim(m.from_address)), ''), m.sender_provider_id, m.id::text)
    HAVING min(m.received_at) >= %(start)s"""


# For a campaign that tests, the counts the firsts do not hold, by copy variant: the sends by channel in the window
# (sends, not people) and the people enrolled now. They sit in the statement that counts the row, so that the row and
# its groups read one snapshot and add up; each read is a range of the campaign's own index.
_SPLIT = f"""
       SELECT t.id IS NOT NULL AS has_target, t.variant, 0 AS enrolled,
              count(*) FILTER (WHERE s.channel = 'email') AS email,
              count(*) FILTER (WHERE s.channel = 'linkedin_connect') AS linkedin_connect,
              count(*) FILTER (WHERE s.channel = 'linkedin_message') AS linkedin_message
         FROM outreach_campaign_sends s
         LEFT JOIN outreach_campaign_targets t ON t.id = s.target_id
        WHERE s.campaign_id = c.id AND {_DELIVERED.format('s')}
          AND s.delivered_at >= %(start)s AND s.delivered_at < %(end)s
        GROUP BY 1, 2
       UNION ALL
       SELECT true, t.variant, count(*), 0, 0, 0
         FROM outreach_campaign_targets t
        WHERE t.campaign_id = c.id AND {_ENROLLED.format('t')}
        GROUP BY 2"""

_CAMPAIGNS = f"""
SELECT c.id::text, c.name, c.status, l.id::text AS list_id, l.name AS list_name,
       (SELECT count(*) FROM outreach_campaign_targets t
         WHERE t.campaign_id = c.id AND {_ENROLLED.format('t')}) AS enrolled,
       {_TESTS.format('c')} AS ab_test, sent.email, sent.linkedin_connect, sent.linkedin_message, split.split
  FROM outreach_campaigns c
  LEFT JOIN lists l ON l.id = c.list_id AND l.deleted_at IS NULL
 CROSS JOIN LATERAL (
       SELECT count(*) FILTER (WHERE s.channel = 'email') AS email,
              count(*) FILTER (WHERE s.channel = 'linkedin_connect') AS linkedin_connect,
              count(*) FILTER (WHERE s.channel = 'linkedin_message') AS linkedin_message
         FROM outreach_campaign_sends s
        WHERE s.campaign_id = c.id AND {_DELIVERED.format('s')}
          AND s.delivered_at >= %(start)s AND s.delivered_at < %(end)s) sent
  LEFT JOIN LATERAL (SELECT jsonb_agg(x) AS split FROM ({_SPLIT}) x WHERE {_TESTS.format('c')}) split ON true
 WHERE c.project_id = %(project)s AND c.activated_at IS NOT NULL
 ORDER BY c.created_at DESC, c.id"""

# One row per campaign and lineage (the target's source, and the watchlist and type of the signal that enrolled it),
# and one per campaign and copy variant, both from one pass over the firsts.
_LINEAGE = f"""
WITH campaigns AS (
    SELECT id FROM outreach_campaigns WHERE project_id = %(project)s AND activated_at IS NOT NULL
), firsts AS ({_sends('contacted')}
    UNION ALL {_replies('human_replies', _REPLY)}
    UNION ALL {_replies('positive_replies', _POSITIVE)}
)
SELECT f.campaign_id::text, t.source, w.id::text AS watchlist_id, w.name AS watchlist_name, g.type AS signal_type,
       GROUPING(t.variant) = 0 AS by_variant, t.id IS NOT NULL AS has_target, t.variant,
       count(*) FILTER (WHERE f.measure = 'contacted') AS contacted,
       count(*) FILTER (WHERE f.measure = 'human_replies') AS human_replies,
       count(*) FILTER (WHERE f.measure = 'positive_replies') AS positive_replies
  FROM firsts f
  LEFT JOIN outreach_campaign_targets t ON t.id = f.target_id
  LEFT JOIN signals g ON g.id = t.signal_id
  LEFT JOIN watchlists w ON w.id = g.watchlist_id
 GROUP BY GROUPING SETS ((1, 2, 3, 4, 5), (1, 7, 8))
 ORDER BY 1, 2 NULLS LAST, 4 NULLS LAST, 5 NULLS LAST"""

_MEASURES = ('contacted', 'human_replies', 'positive_replies')


def campaigns(conn, project_id: str, window: Window) -> list[dict]:
    """The project's campaigns that ever started, newest first, as ``CampaignReportRow`` values."""
    params = {'project': project_id, 'start': window.start_at, 'end': window.end_at, 'positive': list(HIGH_INTENT)}
    lineage: dict[str, list[dict]] = defaultdict(list)
    variants: dict[str, list[dict]] = defaultdict(list)
    for r in conn.execute(_LINEAGE, params):
        (variants if r['by_variant'] else lineage)[r['campaign_id']].append(r)
    return [_row(c, lineage[c['id']], variants[c['id']] + (c['split'] or [])) for c in conn.execute(_CAMPAIGNS, params)]


def _row(c: dict, lineage: list[dict], variants: list[dict]) -> dict:
    campaign_list = {'id': c['list_id'], 'name': c['list_name']} if c['list_id'] else None
    return {
        'sender': 'native',
        'id': c['id'],
        'name': c['name'],
        'status': c['status'],
        'list': campaign_list,
        'counts': {
            'enrolled': c['enrolled'],
            **{m: sum(r[m] for r in lineage) for m in _MEASURES},
            'sent': {k: c[k] for k in ('email', 'linkedin_connect', 'linkedin_message')},
            'bounced': None,
            'meetings': None,
            'deals': None,
        },
        'lineage': [
            {
                'list': campaign_list,
                'source': r['source'],
                'signal': {
                    'watchlist_id': r['watchlist_id'],
                    'watchlist_name': r['watchlist_name'],
                    'type': r['signal_type'],
                }
                if r['watchlist_id']
                else None,
                **{m: r[m] for m in _MEASURES},
            }
            for r in lineage
        ],
        'variants': variant_split.split(variants) if c['ab_test'] else [],
    }
