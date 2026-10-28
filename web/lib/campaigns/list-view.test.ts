import { describe, expect, it } from 'vite-plus/test';
import { approvalBanner, inTab, toTab } from './list-view';

describe('campaigns list view', () => {
  it('reads the tab from the query and falls back to all', () => {
    expect(toTab('paused')).toBe('paused');
    expect(toTab('stopped')).toBe('all');
    expect(toTab(undefined)).toBe('all');
  });

  it('keeps a stopped campaign under all only', () => {
    expect(inTab('stopped', 'all')).toBe(true);
    expect(inTab('stopped', 'completed')).toBe(false);
    expect(inTab('draft', 'draft')).toBe(true);
  });

  it('sums the waiting messages and points at the campaign with the most', () => {
    expect(approvalBanner([{ id: 'a', name: 'A', awaitingApproval: 0 }])).toBeNull();
    const banner = approvalBanner([
      { id: 'a', name: 'A', awaitingApproval: 2 },
      { id: 'b', name: 'B', awaitingApproval: 0 },
      { id: 'c', name: 'C', awaitingApproval: 10 },
    ]);
    expect(banner).toEqual({
      total: 12,
      top: { id: 'c', name: 'C', awaitingApproval: 10 },
      campaigns: 2,
    });
  });
});
