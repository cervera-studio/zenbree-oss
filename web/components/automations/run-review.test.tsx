// @vitest-environment happy-dom
import fc from 'fast-check';
import { describe, expect, it } from 'vite-plus/test';
import { render } from '@/app/auth/testing';
import type { AutomationRunDetail } from '@/lib/automations/api';
import { approval, CANCELLABLE } from '@/lib/automations/run-view';
import { RunFacts } from './run-detail';

const automation = {
  version: 1,
  userId: 'u1',
  approvedVersion: null,
  definition: { listId: 'l1', steps: [{ type: 'add_to_campaign' as const, campaignId: 'c1' }] },
};

const nativeRun = (
  status: AutomationRunDetail['status'],
  enrolled: number,
): AutomationRunDetail => ({
  id: 'r1',
  automationId: 'a1',
  version: 1,
  triggerKind: 'manual',
  status,
  cancelRequested: false,
  creditCap: 10,
  creditsSpent: 0,
  review: true,
  approvedAt: null,
  summary: {},
  errorCode: null,
  createdAt: '2026-10-16T10:00:00Z',
  startedAt: null,
  finishedAt: null,
  delivery: null,
  instantly: null,
  signal: null,
  steps: [
    {
      position: 0,
      kind: 'add_to_campaign',
      status: 'succeeded',
      decision: null,
      input: {},
      output: { enrolled },
      credits: 0,
      attempts: 1,
      errorCode: null,
      startedAt: null,
      finishedAt: null,
    },
  ],
});

const banner = async (run: AutomationRunDetail) => {
  const c = await render(<RunFacts run={run} automation={automation} />);
  return c.querySelector('p.bg-warning-tint');
};

describe('the review banner of a native run', () => {
  it.each(['failed', 'cancelled', 'unknown'] as const)(
    'does not promise approval after a run %s without enrolling anyone',
    async (status) => {
      const run = nativeRun(status, 0);
      expect(approval(run, automation)).toBeNull();
      expect(await banner(run)).toBeNull();
    },
  );

  it.each(['succeeded', 'failed', 'cancelled', 'unknown'] as const)(
    'keeps the banner and approval when a %s run enrolled people',
    async (status) => {
      const run = nativeRun(status, 1);
      expect(approval(run, automation)).toBe('approve');
      expect(await banner(run)).not.toBeNull();
    },
  );

  it.each(['queued', 'running'] as const)(
    'keeps the review note on a %s run with nobody enrolled yet',
    async (status) => {
      const run = { ...nativeRun(status, 0), steps: [] };
      expect(approval(run, automation)).toBeNull();
      expect(await banner(run)).not.toBeNull();
    },
  );

  it('keeps approval of a succeeded version even when nobody was enrolled', async () => {
    const run = nativeRun('succeeded', 0);
    expect(approval(run, automation)).toBe('approve');
    expect(await banner(run)).not.toBeNull();
  });

  it('never promises an unavailable approval on a finished run', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.constantFrom('queued', 'running', 'succeeded', 'failed', 'cancelled', 'unknown'),
        fc.nat({ max: 25 }),
        async (status, enrolled) => {
          const run = nativeRun(status, enrolled);
          if ((await banner(run)) && !CANCELLABLE.includes(status))
            expect(approval(run, automation)).toBe('approve');
        },
      ),
    );
  });
});

describe('the review banner names only what the run has', () => {
  const textFor = async (types: ('add_to_campaign' | 'write_hubspot')[]) => {
    const steps = types.map((type) =>
      type === 'add_to_campaign' ? { type, campaignId: 'c1' } : { type },
    );
    const run = { ...nativeRun('queued', 0), steps: [] };
    const c = await render(
      <RunFacts run={run} automation={{ ...automation, definition: { listId: 'l1', steps } }} />,
    );
    return c.querySelector('p.bg-warning-tint')?.textContent ?? '';
  };

  it('has no HubSpot in a run without a write_hubspot step', async () => {
    expect(await textFor(['add_to_campaign'])).not.toMatch(/hubspot/i);
  });

  it('has no campaign in a run without a campaign step', async () => {
    expect(await textFor(['write_hubspot'])).toMatch(/hubspot/i);
    expect(await textFor(['write_hubspot'])).not.toMatch(/campagna|campaign/i);
  });

  it('names both when the run has both', async () => {
    const both = await textFor(['add_to_campaign', 'write_hubspot']);
    expect(both).toMatch(/hubspot/i);
    expect(both).toMatch(/campagna/i);
  });
});
