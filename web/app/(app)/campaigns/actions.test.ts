import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { setAllocation, updateCampaign } from '@/lib/campaigns/api';
import { livePreview } from '@/lib/campaigns/builder-api';
import { succeed } from '@/lib/types';
import { allocationAction, livePreviewAction, updateCampaignAction } from './actions';

vi.mock('next/cache', () => ({ revalidatePath: vi.fn() }));
vi.mock('next/navigation', () => ({ redirect: vi.fn() }));
vi.mock('@/lib/campaigns/api', () => ({ updateCampaign: vi.fn(), setAllocation: vi.fn() }));
vi.mock('@/lib/campaigns/builder-api', () => ({ livePreview: vi.fn() }));
vi.mock('@/lib/campaigns/senders', () => ({ setCampaignSenders: vi.fn() }));

const CAMPAIGN = '0b0e7c9e-3f43-4d07-8d86-1a1f2f0f6a11';
const step = {
  step: 1,
  channel: 'email',
  subject: 'Hi',
  body: 'From {{city}}',
  sendAfterDays: 0,
  threaded: false,
};
const city = { token: 'city', source: { kind: 'column', key: 'city' }, fallback: null };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(updateCampaign).mockResolvedValue(succeed({} as never));
  vi.mocked(livePreview).mockResolvedValue(succeed({ items: [] }));
});

describe('campaign field actions (#933)', () => {
  it('saves the fields with the steps in one request', async () => {
    await updateCampaignAction(CAMPAIGN, { steps: [step], fields: [city] });
    expect(updateCampaign).toHaveBeenCalledWith(CAMPAIGN, { steps: [step], fields: [city] });
  });

  it('keeps copy B through a builder save (#934)', async () => {
    const tested = {
      ...step,
      b: { subject: 'Idea for {{companyName}}', body: 'Hey {{firstName}}' },
    };
    await updateCampaignAction(CAMPAIGN, { steps: [tested] });
    expect(updateCampaign).toHaveBeenCalledWith(CAMPAIGN, { steps: [tested] });
  });

  it('previews copy B with the typed steps (#934)', async () => {
    const { step: _, sendAfterDays: __, ...typed } = step;
    const tested = { ...typed, b: { subject: 'B', body: 'Copy B for {{city}}' } };
    await livePreviewAction(CAMPAIGN, [tested], [city]);
    expect(livePreview).toHaveBeenCalledWith(CAMPAIGN, [tested], [city]);
  });

  it.each([
    ['a blank fallback', { ...city, fallback: '  ' }],
    ['an unknown signal part', { ...city, source: { kind: 'signal', key: 'email' } }],
  ])('refuses %s before the API', async (_, field) => {
    const result = await updateCampaignAction(CAMPAIGN, { fields: [field] });
    expect(result.success).toBe(false);
    expect(updateCampaign).not.toHaveBeenCalled();
  });
});

describe('allocationAction (#934)', () => {
  it('sends a share on the scale, or a pause, and nothing else', async () => {
    vi.mocked(setAllocation).mockResolvedValue(succeed({} as never));
    await allocationAction(CAMPAIGN, { bPercent: 30 });
    await allocationAction(CAMPAIGN, { paused: true });
    for (const bad of [{ bPercent: 35 }, { bPercent: 100 }, { bPercent: 30, paused: true }, {}]) {
      expect((await allocationAction(CAMPAIGN, bad)).success).toBe(false);
    }
    expect(vi.mocked(setAllocation).mock.calls).toEqual([
      [CAMPAIGN, { bPercent: 30 }],
      [CAMPAIGN, { paused: true }],
    ]);
  });
});
