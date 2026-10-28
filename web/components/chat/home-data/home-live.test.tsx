import { NextIntlClientProvider } from 'next-intl';
import type { ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vite-plus/test';
import { FORMATS } from '@/i18n/config';
import type { Announcement, RunRow } from '@/lib/home/live';
import { finishedOf, listOf, runOf } from '@/lib/home/test-overview';
import { MESSAGES } from '@/messages';
import { HomeCampaigns } from './home-campaigns';
import { HomeLists } from './home-lists';
import { HomeLiveRegion } from './home-live-region';
import { HomeRunning } from './home-running';

const render = (node: ReactNode, locale: 'it' | 'en' = 'it') =>
  renderToStaticMarkup(
    <NextIntlClientProvider
      locale={locale}
      messages={MESSAGES[locale]}
      formats={FORMATS}
      timeZone="Europe/Rome"
    >
      {node}
    </NextIntlClientProvider>,
  );

const text = (html: string) => html.replace(/<[^>]*>/g, '');

const runs = (...rows: RunRow[]) =>
  render(<HomeRunning runs={rows} viewerId={null} onEvent={() => {}} />);

describe('HomeRunning', () => {
  it('shows a live run with its progress and no leave animation', () => {
    const html = runs({ phase: 'running', jobId: 'j1', run: runOf() });
    expect(html).toContain('aria-valuenow="49"');
    expect(text(html)).toContain('2 di 4 passi');
    expect(html).not.toContain('animate-home-leave');
  });

  it('shows a finished run as done at 100 % and leaves it after the hold', () => {
    const html = runs({ phase: 'done', jobId: 'j1', done: finishedOf(), at: 0 });
    expect(html).toContain('aria-valuenow="100"');
    expect(html).toContain('animate-home-leave');
    expect(text(html)).toContain('Completata · 41 persone, 38 con email verificata');
    expect(text(html)).toContain('4 di 4 passi');
    expect(html).not.toContain('animate-spin');
  });

  it('names companies, with no verified count', () => {
    const html = runs({
      phase: 'done',
      jobId: 'j1',
      done: finishedOf({ entity: 'company', rows: 12, verifiedEmails: null }),
      at: 0,
    });
    expect(text(html)).toContain('Completata · 12 aziende');
    expect(text(html)).not.toContain('email verificata');
  });

  it('hides the section when no run is left', () => {
    expect(runs()).toBe('');
  });
});

describe('HomeLists', () => {
  it('badges and highlights a fresh list only', () => {
    const html = render(
      <HomeLists lists={[listOf('a', 'Nuova lista'), listOf('b', 'Vecchia')]} fresh={['a']} />,
    );
    expect(html.match(/animate-home-row-glow/g)).toHaveLength(1);
    expect(html.match(/animate-home-arrive/g)).toHaveLength(1);
    expect(text(html)).toContain('Nuova lista');
    expect(html.match(/>Nuova</g)).toHaveLength(1);
  });
});

describe('HomeCampaigns', () => {
  it('pops the reply count of the campaign that got one', () => {
    const campaigns = ['c1', 'c2'].map((id) => ({
      id,
      name: id,
      status: 'active' as const,
      channels: ['email' as const],
      sent: 10,
      replies: 3,
      owner: null,
    }));
    const html = render(
      <HomeCampaigns campaigns={campaigns} hint={null} pulses={{ 'campaign:c2': 1 }} />,
    );
    expect(html.match(/animate-home-pop/g)).toHaveLength(1);
    expect(html.indexOf('animate-home-pop')).toBeGreaterThan(html.indexOf('/campaigns/c1'));
  });
});

describe('HomeLiveRegion', () => {
  const say = (items: Announcement[], locale: 'it' | 'en' = 'it') =>
    text(render(<HomeLiveRegion announced={items.map((a, i) => ({ ...a, id: i }))} />, locale));

  it('is polite and empty before anything happens', () => {
    const html = render(<HomeLiveRegion announced={[]} />);
    expect(html).toContain('aria-live="polite"');
    expect(text(html)).toBe('');
  });

  it('puts each announcement in its own node, so the region reads only the one added', () => {
    const html = render(
      <HomeLiveRegion
        announced={[
          { kind: 'approvals', added: 2, total: 1216, id: 1 },
          { kind: 'finished', title: 'Agenzie', entity: 'person', rows: 41, id: 2 },
        ]}
      />,
    );
    expect(html.match(/<p>[^<]+<\/p>/g)).toEqual([
      '<p>2 nuovi messaggi da approvare. 1.216 in totale.</p>',
      '<p>Ricerca finita: Agenzie, 41 persone.</p>',
    ]);
  });

  it('names the sender of a reply, and its intent when it has one', () => {
    expect(say([{ kind: 'reply', name: 'Marco Bellini', intent: 'interested', count: 4 }])).toBe(
      'Nuova risposta per te da Marco Bellini, interessato. 4 risposte da leggere.',
    );
    expect(say([{ kind: 'reply', name: 'Marco Bellini', intent: null, count: 1 }])).toBe(
      'Nuova risposta per te da Marco Bellini. 1 risposta da leggere.',
    );
  });
});
