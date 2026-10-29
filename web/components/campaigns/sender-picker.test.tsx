// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { setCampaignSendersAction } from '@/app/(app)/campaigns/actions';
import { click, control, render } from '@/app/auth/testing';
import type { CampaignSender } from '@/lib/campaigns/senders';
import { SenderPicker } from './sender-picker';

vi.mock('@/app/(app)/campaigns/actions', () => ({ setCampaignSendersAction: vi.fn() }));

const CAMPAIGN = '0b0e7c9e-3f43-4d07-8d86-1a1f2f0f6a11';
const anna: CampaignSender = {
  id: '6f1c2b0e-0000-4000-8000-000000000001',
  provider: 'google',
  accountLabel: 'anna@acme.test',
  status: 'connected',
  picked: true,
  state: 'ready',
  reason: null,
  blocker: null,
  resumesAt: null,
  sentToday: 3,
  dailyCap: 25,
  warmingUp: false,
};
const mario: CampaignSender = {
  ...anna,
  id: '6f1c2b0e-0000-4000-8000-000000000002',
  provider: 'microsoft',
  accountLabel: 'mario@acme.test',
  state: 'held',
  reason: 'paused',
  blocker: 'paused',
};

function box(c: HTMLElement, label: string): HTMLElement {
  const el = [...c.querySelectorAll('label')].find((l) => l.textContent === label);
  const target = el && document.getElementById(el.htmlFor);
  if (!target) throw new Error(`no checkbox labelled ${label}`);
  return target;
}

beforeEach(() => {
  vi.mocked(setCampaignSendersAction).mockReset();
  vi.mocked(setCampaignSendersAction).mockResolvedValue({
    success: true,
    data: { senders: [], maxSenders: 20 },
  });
});

describe('SenderPicker', () => {
  it('sends the picked ids without the account unticked', async () => {
    const c = await render(
      <SenderPicker
        campaignId={CAMPAIGN}
        pool={{ senders: [anna, mario], maxSenders: 20 }}
        keepOne={false}
      />,
    );
    await click(box(c, 'mario@acme.test'));
    expect(setCampaignSendersAction).toHaveBeenCalledWith(CAMPAIGN, [anna.id]);
  });

  it('keeps the last account that can send of a live campaign', async () => {
    const c = await render(
      <SenderPicker
        campaignId={CAMPAIGN}
        pool={{ senders: [anna, mario], maxSenders: 20 }}
        keepOne
      />,
    );
    expect(box(c, 'anna@acme.test').hasAttribute('disabled')).toBe(true);
    expect(box(c, 'mario@acme.test').hasAttribute('disabled')).toBe(false);
    expect(c.textContent).toContain('aggiungine');
  });

  it('links an account that needs reconnecting to Integrations', async () => {
    const broken = {
      ...anna,
      status: 'error',
      state: 'held',
      reason: 'needs_reconnect',
      blocker: 'needs_reconnect',
    } as const;
    const c = await render(
      <SenderPicker
        campaignId={CAMPAIGN}
        pool={{ senders: [broken], maxSenders: 20 }}
        keepOne={false}
      />,
    );
    expect(control(c, 'Ricollega').getAttribute('href')).toBe('/settings/integrations');
    expect(c.textContent).toContain('Da ricollegare');
  });

  it('stops at the cap', async () => {
    const free = { ...mario, picked: false, state: 'ready', reason: null, blocker: null } as const;
    const c = await render(
      <SenderPicker
        campaignId={CAMPAIGN}
        pool={{ senders: [anna, free], maxSenders: 1 }}
        keepOne={false}
      />,
    );
    expect(box(c, 'mario@acme.test').hasAttribute('disabled')).toBe(true);
    expect(box(c, 'anna@acme.test').hasAttribute('disabled')).toBe(false);
  });
});
