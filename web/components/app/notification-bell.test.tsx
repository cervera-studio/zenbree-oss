// @vitest-environment happy-dom
import { NextIntlClientProvider } from 'next-intl';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vite-plus/test';
import { BrowserTimeZone } from '@/i18n/browser-time-zone';
import { FORMATS } from '@/i18n/config';
import type { AppNotification } from '@/lib/notifications/group';
import { MESSAGES } from '@/messages';
import { NotificationList } from './notification-bell';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: () => {} }) }));

const at = new Date().toISOString();
const item = (kind: AppNotification['kind'], data: AppNotification['data']): AppNotification => ({
  id: kind,
  kind,
  href: '/',
  data,
  read: false,
  createdAt: at,
  browser: false,
});

function markup(items: AppNotification[]) {
  return renderToStaticMarkup(
    <NextIntlClientProvider locale="it" messages={MESSAGES.it} formats={FORMATS} timeZone="UTC">
      <NotificationList items={items} onPick={() => {}} />
    </NextIntlClientProvider>,
  );
}

function text(items: AppNotification[]) {
  return markup(items)
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

afterEach(() => {
  vi.restoreAllMocks();
});

function mockBrowserTimeZone(timeZone: string) {
  const resolved = Intl.DateTimeFormat.prototype.resolvedOptions;
  vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockImplementation(function (
    this: Intl.DateTimeFormat,
  ) {
    return { ...resolved.call(this), timeZone };
  });
}

describe('notification panel items', () => {
  it.each([
    [
      item('search_done', { chat: 'CEO a Milano', people: 47, companies: 31, refunded: 14 }),
      'Ricerca finita: 47 persone in 31 aziende',
      '14 crediti resi',
    ],
    [item('search_done', {}), 'Ricerca finita', 'I risultati sono pronti nella chat.'],
    [
      item('reply_urgent', { name: 'Marco Bellini', label: 'meeting_request' }),
      'Marco Bellini chiede un incontro',
      'MB',
    ],
    [
      item('approval_needed', { count: 12, campaign: 'Agenzie Milano' }),
      '12 messaggi da approvare',
      'Agenzie Milano · partono dopo il tuo ok',
    ],
    [
      item('search_failed', { chat: 'Marketing nel tech', done: 64, units: 100 }),
      'Ricerca ferma',
      'Marketing nel tech · 64 di 100, le altre non le paghi',
    ],
    [
      item('agent_question', { chat: 'SaaS italiane', reason: 'funnel' }),
      'ha bisogno di te',
      'SaaS italiane · nessun risultato',
    ],
    [item('low_credits', { credits: 150 }), 'Crediti in esaurimento', 'Ti restano 150 crediti.'],
    [
      item('low_credits', { credits: 150, organization: 'Agenzia Bo' }),
      'Crediti in esaurimento',
      'Agenzia Bo · Ti restano 150 crediti.',
    ],
    [
      item('payment_failed', { organization: 'Agenzia Bo' }),
      'Ricarica automatica non riuscita',
      'Agenzia Bo · Controlla il metodo di pagamento.',
    ],
    [
      item('linkedin_accepted', { name: 'Luca Moretti', campaign: 'Brera', channel: 'linkedin' }),
      'Luca Moretti ha accettato il collegamento',
      'Campagna «Brera» · sequenza LinkedIn',
    ],
    [
      item('bounces_high', { campaign: 'Agenzie Milano', invalid: 3, attempted: 40 }),
      'Troppi rimbalzi in «Agenzie Milano»',
      '3 indirizzi non validi su 40 persone',
    ],
    [
      item('bounces_high', { campaign: 'Agenzie Milano', invalid: 2, attempted: 20, paused: true }),
      'Troppi rimbalzi in «Agenzie Milano»',
      '2 email rimbalzate su 20 inviate · campagna in pausa',
    ],
    [
      item('mailbox_error', { mailbox: 'me@acme.test', provider: 'google' }),
      '«me@acme.test» è scollegata',
      'Le campagne che la usano non inviano',
    ],
    [item('mailbox_error', {}), 'Casella scollegata', 'Le campagne che la usano non inviano'],
    [
      item('mailbox_error', { mailbox: 'Anna Rossi', provider: 'unipile' }),
      '«Anna Rossi» è scollegato',
      'Le campagne LinkedIn non inviano finché non ricolleghi l’account.',
    ],
    [
      item('mailbox_error', { provider: 'unipile' }),
      'Account LinkedIn scollegato',
      'Le campagne LinkedIn non inviano',
    ],
    [
      item('reply', { name: 'Zed', label: 'other', unverified: true, campaign: 'Agenzie' }),
      'Nuova risposta da Zed',
      'Mittente non verificato',
    ],
    [
      item('plan_renewed', { plan: 'starter', credits: 2000, expired: 450, organization: 'Bo' }),
      'Piano Starter rinnovato: +2.000 crediti',
      'Bo · 450 crediti non usati scaduti',
    ],
  ])('%#: title and subtitle', (n, title, subtitle) => {
    const shown = text([n]);
    expect(shown).toContain(title);
    expect(shown).toContain(subtitle);
  });

  it('a reply offers its draft and the thread as links apart from the title, with the quote and channel', () => {
    const html = markup([
      {
        ...item('reply', { name: 'Giulia Ferri', channel: 'linkedin', campaign: 'Agenzie' }),
        href: '/inbox?m=m1',
        quote: 'Ci sentiamo giovedì?',
      },
    ]);
    const links = [...html.matchAll(/<a [^>]*href="([^"]+)"[^>]*>/g)].map((m) => m[1]);
    expect(links).toEqual(['/inbox?m=m1', '/inbox?m=m1#reply', '/inbox?m=m1']);
    expect(html).toContain('aria-label="Rispondi: Nuova risposta da Giulia Ferri"');
    expect(html).toContain('«Ci sentiamo giovedì?»');
    expect(html).toContain('aria-label="LinkedIn"');
  });

  it('a meeting request shows Accetta and the meeting badge, not Rispondi', () => {
    const html = markup([item('reply_urgent', { name: 'Marco', label: 'meeting_request' })]);
    expect(html).toContain('>Accetta<');
    expect(html).not.toContain('>Rispondi<');
    expect(html).toContain('aria-label="Incontro"');
  });

  it('a system kind has an icon tile, never a person avatar, and no quote', () => {
    const html = markup([item('search_done', { channel: 'email', name: 'Anna Rossi' })]);
    expect(html).not.toContain('<blockquote');
    expect(html).not.toContain('role="img"');
    expect(text([item('search_done', { channel: 'email', name: 'Anna Rossi' })])).not.toContain(
      'AR',
    );
  });

  it('an off-site href never reaches an action link', () => {
    const evil = { ...item('reply', { name: 'Anna' }), href: 'https://evil.test' };
    const hrefs = [...markup([evil]).matchAll(/href="([^"]*)"/g)].map((m) => m[1]);
    expect(hrefs.length).toBeGreaterThan(1);
    expect(hrefs.every((h) => h === '/')).toBe(true);
  });

  it('groups by day and marks unread items for screen readers', () => {
    expect(text([item('payment_failed', {})])).toMatch(/^Oggi .*\(da leggere\)/);
  });

  it("shows the time in the browser's zone on a first visit, when the server renders UTC (#479)", async () => {
    mockBrowserTimeZone('Europe/Rome');
    const container = document.createElement('div');
    await act(async () => {
      createRoot(container).render(
        <NextIntlClientProvider locale="it" messages={MESSAGES.it} formats={FORMATS} timeZone="UTC">
          <BrowserTimeZone>
            <NotificationList
              items={[{ ...item('search_done', {}), createdAt: '2026-10-08T11:02:00Z' }]}
              onPick={() => {}}
            />
          </BrowserTimeZone>
        </NextIntlClientProvider>,
      );
    });
    expect(container.querySelector('time')?.textContent).toBe('13:02');
  });
});
