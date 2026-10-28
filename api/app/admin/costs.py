"""Cost / revenue aggregation for the backoffice, all in SQL.

Costs are USD (llm_usage_costed, vendor_calls), revenue EUR (purchases);
margin converts costs at settings.eur_per_usd.
"""

from datetime import UTC, date, datetime, time, timedelta

from app.core.config import settings
from app.core.db import get_conn

# Window + optional single-user filter, shared by every CTE.
_W = """created_at >= %(f)s AND created_at < %(t)s
        AND (%(uid)s::uuid IS NULL OR user_id = %(uid)s::uuid)"""

_CTES = f"""
llm AS (SELECT user_id, (created_at AT TIME ZONE 'UTC')::date AS day, cost_usd
        FROM llm_usage_costed WHERE {_W}),
vend AS (SELECT user_id, (created_at AT TIME ZONE 'UTC')::date AS day, cost_usd
         FROM vendor_calls WHERE {_W}),
-- Net of the slack refunded at settle; admin deductions and expired plan
-- credits are not usage.
spent AS (SELECT user_id, (created_at AT TIME ZONE 'UTC')::date AS day,
                 amount - COALESCE(settlement_refund_amount, 0) AS credits
          FROM credit_transactions
          WHERE type = 'deduction' AND status = 'committed'
            AND COALESCE(reference_type, '') NOT IN ('admin', 'plan_expiry') AND {_W}),
-- ponytail: assumes every purchase is in EUR (the only currency we sell in).
rev AS (SELECT user_id, (created_at AT TIME ZONE 'UTC')::date AS day,
               amount_cents - refunded_cents AS cents, credits
        FROM purchases WHERE {_W})
"""

_PER_USER = f"""
WITH {_CTES},
l AS (SELECT user_id, sum(cost_usd)::float8 AS v FROM llm GROUP BY 1),
v AS (SELECT user_id, sum(cost_usd)::float8 AS v FROM vend GROUP BY 1),
s AS (SELECT user_id, sum(credits)::int AS v FROM spent GROUP BY 1),
r AS (SELECT user_id, sum(cents)::int AS cents, sum(credits)::int AS credits FROM rev GROUP BY 1),
ids AS (SELECT user_id FROM l UNION SELECT user_id FROM v UNION SELECT user_id FROM s UNION SELECT user_id FROM r)
SELECT ids.user_id::text AS user_id, u.email,
       COALESCE(l.v, 0) AS llm_cost_usd, COALESCE(v.v, 0) AS vendor_cost_usd,
       COALESCE(s.v, 0) AS credits_spent, COALESCE(r.credits, 0) AS credits_purchased,
       COALESCE(r.cents, 0) AS revenue_eur_cents
FROM ids
LEFT JOIN "user" u ON u.id = ids.user_id
LEFT JOIN l ON l.user_id IS NOT DISTINCT FROM ids.user_id
LEFT JOIN v ON v.user_id IS NOT DISTINCT FROM ids.user_id
LEFT JOIN s ON s.user_id IS NOT DISTINCT FROM ids.user_id
LEFT JOIN r ON r.user_id IS NOT DISTINCT FROM ids.user_id
"""

_DAILY = f"""
WITH {_CTES},
days AS (SELECT generate_series(%(fd)s::date, %(td)s::date, interval '1 day')::date AS day)
SELECT days.day,
       (SELECT COALESCE(sum(cost_usd), 0)::float8 FROM llm WHERE llm.day = days.day) AS llm_cost_usd,
       (SELECT COALESCE(sum(cost_usd), 0)::float8 FROM vend WHERE vend.day = days.day) AS vendor_cost_usd,
       (SELECT COALESCE(sum(credits), 0)::int FROM spent WHERE spent.day = days.day) AS credits_spent,
       (SELECT COALESCE(sum(cents), 0)::int FROM rev WHERE rev.day = days.day) AS revenue_eur_cents
FROM days ORDER BY days.day
"""

_BY_VENDOR = f"""
SELECT vendor, count(*)::int AS calls, sum(units)::int AS units, sum(cost_usd)::float8 AS cost_usd
FROM vendor_calls WHERE {_W} GROUP BY vendor ORDER BY cost_usd DESC
"""

# Hit rate per contact provider (email/phone waterfall), from contact_lookups.
_BY_PROVIDER = f"""
SELECT provider, channel, count(*)::int AS lookups, (count(*) FILTER (WHERE found))::int AS hits
FROM contact_lookups WHERE {_W} GROUP BY provider, channel ORDER BY channel, lookups DESC
"""


def _money(r: dict) -> dict:
    cost_usd = r['llm_cost_usd'] + r['vendor_cost_usd']
    revenue_eur = r['revenue_eur_cents'] / 100
    return {
        **r,
        'cost_usd': round(cost_usd, 6),
        'revenue_eur': revenue_eur,
        'margin_eur': round(revenue_eur - cost_usd * settings.eur_per_usd, 4),
    }


def costs(from_date: date, to_date: date, user_id: str | None = None) -> dict:
    """``to_date`` inclusive, UTC days."""
    p = {
        'f': datetime.combine(from_date, time(), UTC),
        't': datetime.combine(to_date + timedelta(days=1), time(), UTC),
        'fd': from_date,
        'td': to_date,
        'uid': user_id,
    }
    with get_conn() as conn:
        users = [_money(r) for r in conn.execute(_PER_USER, p).fetchall()]
        daily = [_money(r) for r in conn.execute(_DAILY, p).fetchall()]
        by_vendor = conn.execute(_BY_VENDOR, p).fetchall()
        by_provider = conn.execute(_BY_PROVIDER, p).fetchall()
    keys = ('llm_cost_usd', 'vendor_cost_usd', 'credits_spent', 'credits_purchased', 'revenue_eur_cents')
    zero = {'llm_cost_usd': 0.0, 'vendor_cost_usd': 0.0}
    totals = _money({k: sum((u[k] for u in users), zero.get(k, 0)) for k in keys})
    users.sort(key=lambda u: u['cost_usd'], reverse=True)
    return {
        'from': from_date,
        'to': to_date,
        'eur_per_usd': settings.eur_per_usd,
        'totals': totals,
        'users': users,
        'daily': daily,
        'by_vendor': by_vendor,
        'by_provider': by_provider,
    }
