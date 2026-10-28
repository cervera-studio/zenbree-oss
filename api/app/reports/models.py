"""The campaign report's response (#942). A count of ``None`` is unknown, never zero."""

from __future__ import annotations

import builtins
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class CampaignReportWindow(BaseModel):
    start: date
    end: date
    time_zone: str


class CampaignReportList(BaseModel):
    id: str
    name: str


class CampaignReportSignal(BaseModel):
    watchlist_id: str
    watchlist_name: str
    type: str


class CampaignReportSends(BaseModel):
    email: int
    linkedin_connect: int
    linkedin_message: int


class CampaignReportCounts(BaseModel):
    enrolled: int  # people in the campaign now, whatever the window
    contacted: int
    sent: CampaignReportSends  # sends, not people
    human_replies: int
    positive_replies: int
    bounced: int | None  # native bounces are not stored yet (#747)
    meetings: int | None  # HubSpot outcomes are not read yet
    deals: int | None


class CampaignReportLineage(BaseModel):
    list: CampaignReportList | None
    source: Literal['onboarding', 'manual', 'csv', 'refill', 'signal'] | None
    signal: CampaignReportSignal | None
    contacted: int
    human_replies: int
    positive_replies: int


class CampaignReportVariant(BaseModel):
    """One group of an A/B test. Each count summed over a row's groups is the row's count."""

    group: Literal['a', 'b', 'outside', 'unknown']  # outside: no variant; unknown: an event of no person
    enrolled: int  # people in the group now, whatever the window
    contacted: int
    sent: CampaignReportSends
    human_replies: int
    positive_replies: int
    bounced: int | None  # native bounces are not stored yet (#747)


class CampaignReportRow(BaseModel):
    sender: Literal['native', 'instantly']
    id: str  # Instantly's own campaign id for an Instantly campaign
    name: str
    status: str | None
    list: CampaignReportList | None
    counts: CampaignReportCounts
    lineage: builtins.list[CampaignReportLineage]
    variants: builtins.list[CampaignReportVariant]  # empty unless a native campaign tests copy B


class CampaignReportCoverage(BaseModel):
    mode: str
    complete_until: datetime | None
    failing_since: datetime | None
    last_error_code: str | None


class CampaignReport(BaseModel):
    window: CampaignReportWindow
    campaigns: list[CampaignReportRow]
    instantly: CampaignReportCoverage | None  # None: the project never connected Instantly
