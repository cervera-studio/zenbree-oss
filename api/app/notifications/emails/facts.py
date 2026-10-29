"""What a service email says, before any language: the numbers, names and links a language module puts into words."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.locale import Locale
from app.core.working_window import DEFAULT_TIMEZONE
from app.notifications.hrefs import in_org

LOW_CREDITS = 200  # owner decision on #173, independent of auto_topup.THRESHOLDS


def one_line(value, limit: int = 80) -> str:
    return ' '.join(str(value or '').split())[:limit]


def pl(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def amount(n: int, words: tuple[str, str]) -> str:
    return f'{n} {pl(n, *words)}'


def grouped(n: int, separator: str) -> str:
    return f'{n:,}'.replace(',', separator)


def euros(cents: int, thousands: str, decimal: str, *, symbol_after: bool = False) -> str:
    """``symbol_after``: «29 €», as Spanish and French write it; English and Italian write «€29»."""
    whole, rest = divmod(cents, 100)
    amount = grouped(whole, thousands) + (f'{decimal}{rest:02d}' if rest else '')
    return f'{amount}\u00a0€' if symbol_after else f'€{amount}'


def contact(data: dict, anonymous: str) -> str:
    # A stranger wrote the name: one line, short, and never in the subject of mail from our domain.
    return ' '.join(str(data.get('name') or anonymous).split())[:60]


@dataclass(frozen=True)
class SearchDone:
    """E4, #351: the list's name and size, what the run cost and gave back, the balance after. No person names."""

    people: int
    companies: int
    spent: int
    refunded: int
    undelivered: int
    name: str
    chat: str
    balance: int | None
    href: str
    campaign_href: str | None


def search_done(href: str, data: dict, org_id: str = '') -> SearchDone:
    """``org_id``: the organization the list lives in; its links open it first (#743)."""
    campaign = data.get('people_list_id')
    return SearchDone(
        people=data.get('people') or 0,
        companies=data.get('companies') or 0,
        spent=data.get('spent') or 0,
        refunded=data.get('refunded') or 0,
        undelivered=data.get('undelivered') or 0,
        name=one_line(data.get('list')),
        chat=one_line(data.get('chat')),
        balance=data.get('balance'),
        href=in_org(org_id, f'/lists/{data["list_id"]}') if data.get('list_id') else href,
        campaign_href=in_org(org_id, f'/campaigns?list={campaign}') if campaign else None,
    )


@dataclass(frozen=True)
class Renewal:
    plan: str
    credits: int
    at: datetime
    days: int


@dataclass(frozen=True)
class LowCredits:
    """E3, #351: what the balance still buys at the tools' prices, the plan's next grant, the packs."""

    org: str
    credits: int
    emails: int
    phones: int
    renewal: Renewal | None
    upgrade: str | None
    packs: list[tuple[int, int]]  # (credits, price in euro cents), as on sale
    href: str


def low_credits(href: str, data: dict) -> LowCredits:
    from app.agent.tools import handlers, registry  # noqa: F401  handlers registers the tools; agent -> here

    credits = data.get('credits') or 0
    email_tool, phone_tool = registry.get('enrich_email'), registry.get('enrich_phone')
    plans = {p.id: p for p in settings.billing_plans}
    plan = plans.get(data.get('plan') or '')
    zone = ZoneInfo(DEFAULT_TIMEZONE)
    renews = data.get('renews_at') and datetime.fromisoformat(data['renews_at']).astimezone(zone)
    renewal = None
    if plan and renews:
        days = max((renews.date() - datetime.now(zone).date()).days, 0)
        renewal = Renewal(plan.id.capitalize(), plan.credits, renews, days)
    # The biggest plan on sale, offered to an organization with no live plan or a known smaller one: never to one
    # whose plan is unknown (a price not in BILLING_PLANS) or already the top, cancelling or in arrears.
    top = max((p for p in settings.billing_plans if p.stripe_price_id), key=lambda p: p.credits, default=None)
    upgrade = top if top and ('plan' not in data or (plan and plan.credits < top.credits)) else None
    return LowCredits(
        org=one_line(data.get('organization')),
        credits=credits,
        emails=credits // email_tool.price.credits if email_tool and email_tool.price else 0,
        phones=credits // phone_tool.price.credits if phone_tool and phone_tool.price else 0,
        renewal=renewal,
        upgrade=upgrade.id.capitalize() if upgrade else None,
        packs=[(p.credits, p.price_eur_cents) for p in settings.billing_packs if p.stripe_price_id],
        href=href,
    )


@dataclass(frozen=True)
class Digest:
    email: str
    name: str
    frequency: str
    queued: dict[str, int]
    approvals: int
    balances: list[tuple[str, int]]
    at: datetime
    locale: Locale

    @property
    def first_name(self) -> str:
        return self.name.split()[0] if self.name.split() else ''

    @property
    def weekly(self) -> bool:
        return self.frequency == 'weekly'

    @property
    def replies(self) -> int:
        return self.queued.get('reply', 0) + self.queued.get('reply_urgent', 0)

    @property
    def has_news(self) -> bool:
        """Queued things or live approvals; the balance lines alone are not a reason to write."""
        return bool(self.approvals) or any(self.queued.get(k) for k in DIGEST_KINDS)


DIGEST_KINDS = ('reply_urgent', 'reply', 'linkedin_accepted', 'search_done', 'search_failed')
Words = tuple[str, str]  # (one, many)


def digest_lines(d: Digest, approvals: Words, lines: dict[str, Words]) -> list[str]:
    """What the digest has to say, one counted line per kind; ``lines`` has every kind of ``DIGEST_KINDS``."""
    first = [amount(d.approvals, approvals)] if d.approvals else []
    return first + [amount(d.queued[k], lines[k]) for k in DIGEST_KINDS if d.queued.get(k)]


def digest_head(d: Digest, approvals: Words, words: dict[str, Words], join: str) -> str:
    """The two biggest things of the subject. Both reply kinds count as one: ``words['reply']``."""
    heads = [
        (d.approvals, approvals),
        (d.replies, words['reply']),
        *((d.queued.get(k, 0), words[k]) for k in ('linkedin_accepted', 'search_done', 'search_failed')),
    ]
    return join.join([amount(n, w) for n, w in heads if n][:2])
