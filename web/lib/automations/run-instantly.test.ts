import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { getInstantlyBatch, getInstantlyCampaigns } from '@/lib/integrations/instantly';
import type { AutomationRunDetail, RunStep } from './api';
import { readRunInstantly } from './run-instantly';

vi.mock('server-only', () => ({}));
vi.mock('@/lib/integrations/instantly', () => ({
  getInstantlyBatch: vi.fn(),
  getInstantlyCampaigns: vi.fn(),
}));

beforeEach(() => vi.clearAllMocks());

const automation = {
  version: 2,
  userId: 'u1',
  definition: { listId: 'l1', steps: [{ type: 'add_to_instantly' as const, campaignId: 'saved' }] },
};

const step = (output: RunStep['output'], status: RunStep['status'] = 'succeeded'): RunStep => ({
  position: 0,
  kind: 'add_to_instantly',
  status,
  decision: null,
  input: {},
  output,
  credits: 0,
  attempts: 1,
  errorCode: null,
  startedAt: null,
  finishedAt: null,
});

const reviewRun = (steps: RunStep[]): AutomationRunDetail => ({
  id: 'r1',
  automationId: 'a1',
  version: 2,
  triggerKind: 'manual',
  status: 'succeeded',
  cancelRequested: false,
  creditCap: 40,
  creditsSpent: 0,
  review: true,
  approvedAt: null,
  summary: {},
  errorCode: null,
  createdAt: '2026-10-16T10:00:00Z',
  startedAt: null,
  finishedAt: null,
  steps,
  delivery: null,
  instantly: null,
  signal: null,
});

const live = (campaigns: { id: string; maySend: boolean }[]) =>
  vi.mocked(getInstantlyCampaigns).mockResolvedValue({
    success: true,
    data: { campaigns: campaigns.map((c) => ({ ...c, name: c.id, status: 1 })) },
  });

describe('the Instantly campaign an approval of a run that holds nobody opens', () => {
  it('reads the campaign the step read, though the run holds nobody', async () => {
    live([{ id: 'read', maySend: true }]);
    const run = reviewRun([step({ held: 0, campaign: { id: 'read', maySend: true } })]);
    expect(await readRunInstantly(run, automation)).toEqual({
      onApproval: 'sends',
      batch: null,
      moving: false,
    });
  });

  it('falls back to the saved campaign when the step was skipped with no output', async () => {
    live([{ id: 'saved', maySend: false }]);
    const run = reviewRun([step({}, 'skipped')]);
    expect((await readRunInstantly(run, automation)).onApproval).toBe('quiet');
  });

  it('reads nothing for a run with no Instantly step', async () => {
    const run = reviewRun([{ ...step({}), kind: 'qualify' }]);
    expect((await readRunInstantly(run, automation)).onApproval).toBe('none');
    expect(getInstantlyCampaigns).not.toHaveBeenCalled();
    expect(getInstantlyBatch).not.toHaveBeenCalled();
  });
});
