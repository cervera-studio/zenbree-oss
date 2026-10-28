import { describe, expect, it } from 'vite-plus/test';
import type { AutomationRunDetail } from './api';
import { instantlyMoving, instantlyOnApproval } from './run-view';

const held = (over: Partial<NonNullable<AutomationRunDetail['instantly']>>) => ({
  people: 2,
  batchId: null,
  waitingFor: null,
  campaign: { id: 'c1', name: 'Verify running', status: 1, maySend: true },
  ...over,
});

describe('the Instantly campaign an approval sends to', () => {
  it('sends when the live read says it may send, and is quiet when not', () => {
    expect(instantlyOnApproval([{ id: 'c1', maySend: true }], 'c1')).toBe('sends');
    expect(instantlyOnApproval([{ id: 'c1', maySend: false }], 'c1')).toBe('quiet');
  });

  it('cannot be checked when the read failed or the campaign is gone', () => {
    expect(instantlyOnApproval(null, 'c1')).toBe('unreadable');
    expect(instantlyOnApproval([{ id: 'c2', maySend: false }], 'c1')).toBe('unreadable');
  });
});

describe('a run page that follows Instantly on its own', () => {
  it('refreshes while the batch is live or about to start', () => {
    expect(instantlyMoving(held({ batchId: 'b1' }), 'running')).toBe(true);
    expect(instantlyMoving(held({ batchId: 'b1' }), 'queued')).toBe(true);
    expect(instantlyMoving(held({ waitingFor: 'starting' }), null)).toBe(true);
    expect(instantlyMoving(held({ waitingFor: 'other_batch' }), null)).toBe(true);
  });

  it('stops once the batch ended, or while the people wait for the user', () => {
    expect(instantlyMoving(held({ batchId: 'b1' }), 'completed')).toBe(false);
    expect(instantlyMoving(held({ batchId: 'b1' }), null)).toBe(false);
    expect(instantlyMoving(held({ waitingFor: 'approval' }), null)).toBe(false);
    expect(instantlyMoving(held({ waitingFor: 'connection' }), null)).toBe(false);
    expect(instantlyMoving(null, null)).toBe(false);
  });
});
