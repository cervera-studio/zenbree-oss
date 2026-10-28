// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vite-plus/test';
import { click, control, TestIntl } from '@/app/auth/testing';
import type { Activity, ActivityEvent } from '@/lib/campaigns/api';
import { CampaignActivity } from './campaign-activity';

const ev = (id: string, minute: number): ActivityEvent => ({
  id,
  kind: 'sent',
  at: `2026-10-16T10:${String(minute).padStart(2, '0')}:00Z`,
  targetId: null,
  firstName: 'Ada',
  lastName: id,
  companyName: null,
  step: 1,
  channel: 'email',
  label: null,
});

const load = vi.fn();
vi.mock('@/app/(app)/campaigns/actions', () => ({
  loadActivityAction: (...args: unknown[]) => load(...args),
}));

describe('CampaignActivity', () => {
  it('keeps the loaded rows when a refresh of the first page fails', async () => {
    const container = document.createElement('div');
    document.body.replaceChildren(container);
    const root = createRoot(container);
    const show = (first: Activity | null) =>
      act(async () => {
        root.render(
          <TestIntl>
            <CampaignActivity campaignId="c1" first={first} />
          </TestIntl>,
        );
      });
    load
      .mockResolvedValueOnce({ success: true, data: { items: [ev('older', 1)], nextCursor: 'c2' } })
      .mockResolvedValueOnce({
        success: false,
        error: { code: 'INTERNAL', message: 'down', status: 500 },
      });
    await show({ items: [ev('newest', 9)], nextCursor: 'c1' });
    await click(control(container, 'Carica altri'));
    await show(null);
    // The next page fails too: still one alert, and the rows stay.
    await click(control(container, 'Carica altri'));
    expect([...container.querySelectorAll('li')].map((li) => li.textContent)).toEqual([
      expect.stringContaining('Ada newest'),
      expect.stringContaining('Ada older'),
    ]);
    expect(container.querySelectorAll('[role="alert"]')).toHaveLength(1);
  });
});
