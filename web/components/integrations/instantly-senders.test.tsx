import { NextIntlClientProvider } from 'next-intl';
import type { ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { FORMATS } from '@/i18n/config';
import { MESSAGES } from '@/messages';
import { InstantlyManagedSenders, InstantlyOverlap } from './instantly-senders';

vi.mock('next-intl/server', async () => {
  const { createTranslator } = await import('next-intl');
  const { MESSAGES } = await import('@/messages');
  const { FORMATS } = await import('@/i18n/config');
  return {
    getTranslations: async (namespace: 'senders.instantly') =>
      createTranslator({ locale: 'it', messages: MESSAGES.it, formats: FORMATS, namespace }),
  };
});

const answer = vi.fn();
vi.mock('@/lib/integrations/instantly', () => ({ getInstantlyAccounts: () => answer() }));

const ok = (state: string, accounts: Record<string, unknown>[] = []) =>
  answer.mockResolvedValue({ success: true, data: { state, accounts } });

const ANN = { email: 'ann@acme.test', status: 'active', warmup: 'active', dailyLimit: 1500 };
const BOB = { email: 'bob@acme.test', status: 'sending_error', warmup: 'banned', dailyLimit: null };

function text(tree: ReactNode) {
  return renderToStaticMarkup(
    <NextIntlClientProvider locale="it" messages={MESSAGES.it} formats={FORMATS}>
      {tree}
    </NextIntlClientProvider>,
  );
}
const plain = (html: string) =>
  html
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

beforeEach(() => answer.mockReset());

describe('InstantlyManagedSenders', () => {
  it('lists each mailbox with Instantly status, warm-up and daily limit, and no control', async () => {
    ok('ok', [ANN, BOB]);
    const html = text(await InstantlyManagedSenders());
    const shown = plain(html);
    expect(shown).toContain('Gestite da Instantly');
    expect(shown).toContain(
      'ann@acme.test Attiva Warm-up attivo Limite giornaliero in Instantly: 1.500',
    );
    expect(shown).toContain(
      'bob@acme.test Errore di invio Warm-up bloccato Limite giornaliero in Instantly: non impostato',
    );
    expect(html).not.toMatch(/<(button|input|a)\b/);
    expect(shown).not.toMatch(/pront|recapit|inbox/i);
  });

  it('says so when the workspace has no mailbox', async () => {
    ok('ok');
    expect(plain(text(await InstantlyManagedSenders()))).toContain(
      'Il tuo account Instantly non ha caselle di invio.',
    );
  });

  it('shows nothing when Instantly is not connected', async () => {
    ok('not_connected');
    expect(await InstantlyManagedSenders()).toBeNull();
  });

  it('asks for accounts:read with a link to Integrations when the key may not read mailboxes', async () => {
    ok('no_permission');
    const html = text(await InstantlyManagedSenders());
    expect(plain(html)).toContain('accounts:read');
    expect(html).toContain('href="/settings/integrations"');
  });

  it.each([
    ['unavailable', () => ok('unavailable')],
    [
      'a failed request',
      () => answer.mockResolvedValue({ success: false, error: { code: 'X', status: 502 } }),
    ],
  ])('says it could not load now for %s', async (_, arrange) => {
    arrange();
    expect(plain(text(await InstantlyManagedSenders()))).toBe(
      'Impossibile caricare ora le caselle di Instantly.',
    );
  });
});

describe('InstantlyOverlap', () => {
  it('warns on a native mailbox that Instantly also sends from, whatever its case and spaces', async () => {
    ok('ok', [{ ...ANN, email: ' Ann@Acme.TEST ' }]);
    const shown = plain(text(await InstantlyOverlap({ address: 'ann@acme.test' })));
    expect(shown).toContain('Gestita da Instantly');
    expect(shown).toContain('Zenbree non legge quel limite');
  });

  it('shows nothing for an address Instantly does not have', async () => {
    ok('ok', [ANN]);
    expect(await InstantlyOverlap({ address: 'ann@acme.test.it' })).toBeNull();
  });

  it.each(['no_permission', 'unavailable', 'not_connected'])(
    'shows nothing when the state is %s',
    async (state) => {
      ok(state, [ANN]);
      expect(await InstantlyOverlap({ address: 'ann@acme.test' })).toBeNull();
    },
  );
});
