import { createTranslator } from 'next-intl';
import type { ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { TestIntl } from '@/app/auth/testing';
import { FORMATS } from '@/i18n/config';
import type { Campaign, PendingSend } from '@/lib/campaigns/api';
import { MESSAGES } from '@/messages';

const ID = '0b0e7c9e-3f43-4d07-8d86-1a1f2f0f6a11';
const queue = vi.hoisted(() => ({ waiting: 0, held: 0 }));

function send(hold: PendingSend['hold']): PendingSend {
  return { hold } as PendingSend;
}

vi.mock('@/lib/campaigns/api', () => ({
  getCampaign: async () => ({
    success: true,
    data: {
      id: ID,
      projectId: 'p1',
      name: 'Spring outreach',
      status: 'active',
      steps: [],
      awaitingApproval: queue.waiting,
      awaitingApprovalHeld: queue.held,
    } as Partial<Campaign>,
  }),
  getApprovals: async () => ({
    success: true,
    data: {
      items: [
        ...Array.from({ length: queue.waiting }, () => send(null)),
        ...Array.from({ length: queue.held }, () => send('held_by_instantly')),
      ],
    },
  }),
  getTargets: async () => ({ success: true, data: { items: [] } }),
  getActivity: async () => ({ success: true, data: null }),
}));
vi.mock('@/lib/campaigns/builder-api', () => ({}));
vi.mock('@/lib/integrations/linkedin', () => ({}));
vi.mock('@/lib/campaigns/senders', () => ({
  getCampaignSenders: async () => ({ success: true, data: { senders: [], maxSenders: 20 } }),
}));
vi.mock('@/lib/notifications/push-config', () => ({
  getPushConfig: async () => ({ publicKey: null }),
}));
vi.mock('@/components/app/page', () => ({
  AppPage: ({ children }: { children: ReactNode }) => children,
}));
vi.mock('@/components/campaigns/approval-queue', () => ({ ApprovalQueue: () => null }));
vi.mock('@/components/campaigns/campaign-activity', () => ({ CampaignActivity: () => null }));
vi.mock('@/components/campaigns/campaign-builder', () => ({ CampaignBuilder: () => null }));
vi.mock('@/components/campaigns/campaign-detail', () => ({
  CampaignHeader: () => null,
  CampaignSide: () => null,
  CampaignTiles: () => null,
  CampaignTopbarActions: () => null,
}));
vi.mock('@/components/campaigns/campaign-people', () => ({ CampaignPeople: () => null }));
vi.mock('@/components/campaigns/parallel-campaigns', () => ({ ParallelCampaigns: () => null }));
vi.mock('@/components/campaigns/push-prompt', () => ({ PushPrompt: () => null }));
vi.mock('next-intl/server', () => ({
  getTranslations: async (namespace: string) =>
    createTranslator({
      locale: 'it',
      messages: MESSAGES.it,
      namespace: namespace as 'campaigns',
      formats: FORMATS,
    }),
}));

import CampaignPage from './page';

async function banner(): Promise<string | undefined> {
  const page = await CampaignPage({ params: Promise.resolve({ id: ID }) });
  const html = renderToStaticMarkup(<TestIntl>{page}</TestIntl>);
  return html.match(/role="status".*?<strong[^>]*>([^<]*)<\/strong>/)?.[1];
}

describe('the campaign page banner', () => {
  beforeEach(() => {
    queue.waiting = 0;
    queue.held = 0;
  });

  it('shows no banner when the only waiting message is held (#931)', async () => {
    queue.held = 1;
    expect(await banner()).toBeUndefined();
  });

  it('counts the messages waiting for approval, not the held ones (#931)', async () => {
    queue.waiting = 2;
    queue.held = 3;
    expect(await banner()).toBe('2 messaggi aspettano la tua approvazione');
  });
});
