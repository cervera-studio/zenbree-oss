import { NextIntlClientProvider } from 'next-intl';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vite-plus/test';
import { FORMATS } from '@/i18n/config';
import type { HomeLive } from '@/lib/home/live';
import type { HomeOverview } from '@/lib/home/overview';
import { overviewOf } from '@/lib/home/test-overview';
import { MESSAGES } from '@/messages';
import { HomeTodo } from './home-todo';

type Approvals = HomeOverview['approvals'];
type Replies = HomeOverview['replies'];

const NO_REPLIES: Replies = { count: 0, latest: null };
const NO_APPROVALS: Approvals = { total: 0, campaigns: [] };

function render(approvals: Approvals, replies: Replies, pulses: HomeLive['pulses'] = {}) {
  const overview = overviewOf({ approvals, replies });
  return renderToStaticMarkup(
    <NextIntlClientProvider
      locale="it"
      messages={MESSAGES.it}
      formats={FORMATS}
      timeZone="Europe/Rome"
    >
      <HomeTodo overview={overview} pulses={pulses} />
    </NextIntlClientProvider>,
  );
}

const text = (html: string) => html.replace(/<[^>]*>/g, '');

describe('HomeTodo', () => {
  it('shows the quiet line, and no card, when nothing waits', () => {
    const html = render(NO_APPROVALS, NO_REPLIES);
    expect(text(html)).toContain('Niente in attesa');
    expect(html).not.toContain('href=');
  });

  it('shows only the approvals card when there are no replies', () => {
    const html = render(
      { total: 3, campaigns: [{ id: 'c1', name: 'Milano', count: 3 }] },
      NO_REPLIES,
    );
    expect(html).toContain('href="/campaigns/c1"');
    expect(html).not.toContain('Risposte per te');
    expect(text(html)).not.toContain('Niente in attesa');
  });

  it('shows only the replies card when nothing needs approval, and tags a high-intent reply', () => {
    const html = render(NO_APPROVALS, {
      count: 2,
      latest: {
        name: 'Giulia Ferri',
        campaign: 'Fiere 2026',
        intent: null,
        at: '2026-10-01T09:12:00Z',
      },
    });
    expect(html).toContain('href="/inbox"');
    expect(html).not.toContain('Messaggi da approvare');
    expect(text(html)).toContain('Ultima: Giulia Ferri · Fiere 2026');
    expect(text(html)).not.toMatch(/\d:\d\d/);
    expect(text(html)).not.toContain('Interessato');

    const tagged = render(NO_APPROVALS, {
      count: 1,
      latest: {
        name: 'Marco',
        campaign: null,
        intent: 'meeting_request',
        at: '2026-10-01T09:12:00Z',
      },
    });
    expect(text(tagged)).toContain('Richiesta di incontro');
  });

  it('shows both cards', () => {
    const html = render(
      { total: 3, campaigns: [{ id: 'c1', name: 'Milano', count: 3 }] },
      { count: 1, latest: null },
    );
    expect(html).toContain('href="/campaigns/c1"');
    expect(html).toContain('href="/inbox"');
  });

  describe('live pulses', () => {
    const approvals = { total: 16, campaigns: [] };
    const replies = { count: 4, latest: null };

    it('pops and rings only the card whose value changed', () => {
      const html = render(approvals, replies, { approvals: 1 });
      expect(html.match(/animate-home-pop/g)).toHaveLength(1);
      expect(html.match(/animate-home-glow/g)).toHaveLength(1);
      expect(html.indexOf('animate-home-pop')).toBeLessThan(html.indexOf('href="/inbox"'));
    });
  });

  describe('approvals subtitle', () => {
    const subtitle = (approvals: Approvals) => text(render(approvals, NO_REPLIES));

    it('names two campaigns, and counts the rest only when there is one', () => {
      const out = subtitle({
        total: 14,
        campaigns: [
          { id: 'a', name: 'Milano', count: 12 },
          { id: 'b', name: 'Torino', count: 2 },
        ],
      });
      expect(out).toContain('12 in «Milano», 2 in «Torino»');
      expect(out).not.toContain('altre campagne');
      const rest = subtitle({
        total: 1216,
        campaigns: [
          { id: 'a', name: 'Milano', count: 1000 },
          { id: 'b', name: 'Torino', count: 200 },
        ],
      });
      expect(rest).toContain('1.000 in «Milano», 200 in «Torino», 16 in altre campagne');
    });
  });
});
