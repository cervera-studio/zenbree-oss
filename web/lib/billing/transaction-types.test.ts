import { describe, expect, it } from 'vite-plus/test';
import { txKind } from './transaction-types';

describe('txKind', () => {
  it('names a row by its type and reference type', () => {
    expect(txKind({ type: 'reservation', referenceType: 'job' })).toBe('jobHold');
    expect(txKind({ type: 'refund', referenceType: 'reservation.slack' })).toBe('slackRefund');
    expect(txKind({ type: 'refund', referenceType: 'email_bounce' })).toBe('bounceRefund');
    expect(txKind({ type: 'refund', referenceType: 'job' })).toBe('released');
    expect(txKind({ type: 'renewal', referenceType: 'stripe_plan_upgrade' })).toBe('plan');
  });
  it('labels a settled hold like its pending one', () => {
    expect(txKind({ type: 'deduction', referenceType: 'job' })).toBe('jobUsed');
    expect(txKind({ type: 'deduction', referenceType: 'enrich_phone' })).toBe('phoneLookup');
    expect(txKind({ type: 'deduction', referenceType: 'ai_column' })).toBe('aiColumnFill');
    expect(txKind({ type: 'deduction', referenceType: 'saved_search' })).toBe('scheduledSearch');
    expect(txKind({ type: 'deduction', referenceType: 'watchlist' })).toBe('watchlistRun');
    expect(txKind({ type: 'reservation', referenceType: 'automation' })).toBe('automationRun');
    expect(txKind({ type: 'deduction', referenceType: 'automation' })).toBe('automationRun');
  });
  it('leaves an admin adjustment raw', () => {
    expect(txKind({ type: 'adjustment', referenceType: 'admin' })).toBeNull();
    expect(txKind({ type: 'deduction', referenceType: 'admin' })).toBeNull();
  });
});
