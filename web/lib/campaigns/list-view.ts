// The campaigns list (C1): status tabs and the approval banner, from `GET /v1/campaigns`.
import type { CampaignSummary } from './api';

export const TABS = ['all', 'active', 'draft', 'paused', 'completed'] as const;
export type Tab = (typeof TABS)[number];

export function toTab(value?: string): Tab {
  return TABS.find((t) => t === value) ?? 'all';
}

// ponytail: a `stopped` campaign shows only under «Tutte»; the handoff has no tab for it.
export function inTab(status: CampaignSummary['status'], tab: Tab): boolean {
  return tab === 'all' || status === tab;
}

/** All waiting messages, how many campaigns hold them, and the one holding the most («Rivedi»). */
export function approvalBanner(
  campaigns: Pick<CampaignSummary, 'id' | 'name' | 'awaitingApproval'>[],
) {
  const waiting = campaigns.filter((c) => c.awaitingApproval > 0);
  if (waiting.length === 0) return null;
  const top = waiting.reduce((a, b) => (b.awaitingApproval > a.awaitingApproval ? b : a));
  const total = waiting.reduce((sum, c) => sum + c.awaitingApproval, 0);
  return { total, top, campaigns: waiting.length };
}
