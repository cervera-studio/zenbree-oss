import { NextIntlClientProvider } from 'next-intl';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vite-plus/test';
import { FORMATS } from '@/i18n/config';
import type { HomeStats } from '@/lib/api/home';
import { MESSAGES } from '@/messages';
import { barPercent, deltaPercent, HomeStatsPanels } from './home-stats';

const zero = { value: 0, previous: 0 };
const empty: HomeStats = {
  kpis: { leads: zero, contacts: zero, messagesSent: zero, replies: zero, positiveReplies: zero },
  weeks: Array.from({ length: 8 }, (_, i) => ({
    week: `2026-09-${String(7 + i).padStart(2, '0')}T00:00:00Z`,
    messagesSent: 0,
    replies: 0,
  })),
  funnel: { leads: 0, contacts: 0, contacted: 0, replied: 0, positive: 0 },
  activity: [],
};

function html(stats: HomeStats) {
  return renderToStaticMarkup(
    <NextIntlClientProvider locale="en" messages={MESSAGES.en} formats={FORMATS} timeZone="UTC">
      <HomeStatsPanels stats={stats} />
    </NextIntlClientProvider>,
  );
}

describe('home stats', () => {
  it('has no percentage without a previous period', () => {
    expect(deltaPercent({ value: 5, previous: 0 })).toBeNull();
    expect(deltaPercent({ value: 15, previous: 10 })).toBe(50);
    expect(deltaPercent({ value: 5, previous: 10 })).toBe(-50);
  });

  it('scales bars to the tallest and keeps a small value visible', () => {
    expect(barPercent(0, 0)).toBe(0);
    expect(barPercent(1, 1000)).toBe(4);
    expect(barPercent(10, 10)).toBe(100);
  });

  it('renders an all-zero project without NaN or percentages', () => {
    const out = html(empty);
    expect(out).not.toMatch(/NaN|Infinity|%</);
    expect(out).toContain('Nothing yet');
  });

  it('shows the change and the activity of a busy project', () => {
    const out = html({
      ...empty,
      kpis: { ...empty.kpis, leads: { value: 150, previous: 100 } },
      activity: [
        { kind: 'search_finished', at: '2026-10-15T10:00:00Z', title: 'Fiere', link: '/lists/1' },
      ],
    });
    expect(out).toContain('+50%');
    expect(out).toContain('href="/lists/1"');
  });
});
