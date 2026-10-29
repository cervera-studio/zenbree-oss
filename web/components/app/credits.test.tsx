import { NextIntlClientProvider } from 'next-intl';
import type { ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vite-plus/test';
import { FORMATS } from '@/i18n/config';
import { MESSAGES } from '@/messages';
import { CreditsCard, CreditsCardFallback } from './credits';

vi.mock('next-intl/server', async () => {
  const { createFormatter, createTranslator } = await import('next-intl');
  const { MESSAGES } = await import('@/messages');
  const { FORMATS } = await import('@/i18n/config');
  return {
    getTranslations: async (namespace: 'nav' | 'billing') =>
      createTranslator({ locale: 'it', messages: MESSAGES.it, formats: FORMATS, namespace }),
    getFormatter: async () => createFormatter({ locale: 'it', formats: FORMATS, timeZone: 'UTC' }),
  };
});

type Me = Parameters<typeof CreditsCard>[0]['me'];

const ok = (data: Record<string, unknown>) =>
  Promise.resolve({ success: true, data }) as unknown as Me;

async function card(me: Me, orgAdmin = true) {
  const tree = await CreditsCard({ me, orgAdmin });
  const html = renderToStaticMarkup(
    <NextIntlClientProvider locale="it" messages={MESSAGES.it} formats={FORMATS}>
      {tree}
    </NextIntlClientProvider>,
  );
  return html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
}

describe('CreditsCard', () => {
  it('names the plan credits left and their reset date, never that the plan renews', async () => {
    const text = await card(
      ok({
        creditsRemaining: 942,
        planCredits: 2000,
        planCreditsLeft: 942,
        planCreditsResetAt: '2026-11-14T00:00:00Z',
        purchasedCredits: 0,
      }),
    );
    expect(text).toContain('942 / 2.000');
    expect(text).toContain('942 crediti del piano: si azzerano il 14 nov');
    expect(text).toContain('Ricarica');
    expect(text).not.toMatch(/rinnov/i);
  });

  it('shows only the plan part in the reset line when purchased credits add to the balance', async () => {
    const text = await card(
      ok({
        creditsRemaining: 942,
        planCredits: 2000,
        planCreditsLeft: 600,
        planCreditsResetAt: '2026-11-14T00:00:00Z',
        purchasedCredits: 342,
      }),
    );
    expect(text).toContain('942 / 2.000');
    expect(text).toContain('600 crediti del piano: si azzerano il 14 nov');
    expect(text).not.toContain('942 crediti del piano');
    expect(text).not.toContain('acquistati');
  });

  it('uses the singular for one plan credit', async () => {
    const text = await card(
      ok({
        creditsRemaining: 1,
        planCredits: 2000,
        planCreditsLeft: 1,
        planCreditsResetAt: '2026-11-14T00:00:00Z',
      }),
    );
    expect(text).toContain('1 credito del piano: si azzera il 14 nov');
  });

  it('gives a screen reader the balance against the plan', async () => {
    const html = renderToStaticMarkup(
      <NextIntlClientProvider locale="it" messages={MESSAGES.it} formats={FORMATS}>
        {
          await CreditsCard({
            me: ok({ creditsRemaining: 942, planCredits: 2000, planCreditsLeft: 600 }),
            orgAdmin: false,
          })
        }
      </NextIntlClientProvider>,
    );
    expect(html).toMatch(/sr-only[^>]*>Saldo 942\. Il piano dà 2\.000 crediti a ogni periodo\./);
  });

  it('shows the balance and the top-up link alone without a plan', async () => {
    const text = await card(
      ok({
        creditsRemaining: 40,
        planCredits: null,
        planCreditsLeft: null,
        planCreditsResetAt: null,
      }),
    );
    expect(text).toContain('40');
    expect(text).not.toContain(' / ');
    expect(text).not.toContain('azzera');
    expect(text).toContain('Ricarica');
  });

  it('draws no date row for a member without a plan', async () => {
    const html = await card(
      ok({
        creditsRemaining: 40,
        planCredits: null,
        planCreditsLeft: null,
        planCreditsResetAt: null,
      }),
      false,
    );
    expect(html).toBe(' Crediti 40 ');
  });

  it('draws no reset line for a plan without a date', async () => {
    const text = await card(
      ok({
        creditsRemaining: 40,
        planCredits: 2000,
        planCreditsLeft: 40,
        planCreditsResetAt: null,
      }),
    );
    expect(text).not.toContain('azzera');
  });

  it('shows a dash when the request fails', async () => {
    const failed = Promise.resolve({
      success: false,
      error: { code: 'X', message: 'x' },
    }) as unknown as Me;
    const text = await card(failed);
    expect(text).toContain('Crediti —');
    expect(text).not.toContain('azzera');
  });

  it.each([true, false])(
    'is as tall as the loaded card with a plan, for orgAdmin=%s',
    async (orgAdmin) => {
      const loaded = await CreditsCard({
        me: ok({
          creditsRemaining: 942,
          planCredits: 2000,
          planCreditsLeft: 942,
          planCreditsResetAt: '2026-11-14T00:00:00Z',
        }),
        orgAdmin,
      });
      const rows = (card: ReactElement<{ children: unknown }>) =>
        [card.props.children].flat().filter((row) => {
          const props = (row as ReactElement<{ className?: string }> | false)
            ? (row as ReactElement<{ className?: string }>).props
            : null;
          return props && !props.className?.includes('sr-only');
        }).length;
      expect(rows(CreditsCardFallback({ orgAdmin }))).toBe(rows(loaded));
    },
  );
});
