import { afterEach, describe, expect, it, vi } from 'vite-plus/test';

vi.mock('server-only', () => ({}));
vi.mock('@/lib/auth-token', () => ({ getToken: vi.fn() }));
process.env.API_BASE_URL = 'http://api.test';
const { getRun } = await import('./api');

const run = (summary: object) => ({
  id: 'r1',
  automation_id: 'a1',
  version: 1,
  trigger_kind: 'signal',
  status: 'failed',
  cancel_requested: false,
  credit_cap: 10,
  credits_spent: 0,
  review: null,
  approved_at: null,
  summary,
  error_code: 'vendor_failed',
  created_at: '2026-10-21T08:00:00Z',
  started_at: null,
  finished_at: null,
  steps: [],
  delivery: null,
  instantly: null,
  signal: { account_id: 'c1', account_name: 'Hiring Srl', signals: [] },
});

describe('the signals a run reports in its summary', () => {
  afterEach(() => vi.restoreAllMocks());

  it.each([
    ['gave them back', { given_back: true }, { givenBack: true }],
    [
      'gave up on its account',
      { given_up: true, failed_runs: 3 },
      { givenUp: true, failedRuns: 3 },
    ],
  ])('reads a run that %s', async (_, signals, read) => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json(run({ signals })));
    const got = await getRun('a1', 'r1');
    expect(got.success && got.data.summary.signals).toEqual(read);
  });
});
