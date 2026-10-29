import { NextIntlClientProvider } from 'next-intl';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vite-plus/test';
import { FORMATS } from '@/i18n/config';
import type { HomeOverview } from '@/lib/home/overview';
import { MESSAGES } from '@/messages';
import { HomeCampaigns } from './home-campaigns';

type Campaign = HomeOverview['campaigns'][number];

const campaign = (sent: number, replies: number): Campaign => ({
  id: 'c1',
  name: 'Fiere 2026',
  status: 'active',
  channels: ['email'],
  sent,
  replies,
  owner: null,
});

function text(locale: 'it' | 'en', c: Campaign) {
  return renderToStaticMarkup(
    <NextIntlClientProvider
      locale={locale}
      messages={MESSAGES[locale]}
      formats={FORMATS}
      timeZone="Europe/Rome"
    >
      <HomeCampaigns campaigns={[c]} hint={null} pulses={{}} />
    </NextIntlClientProvider>,
  )
    .replace(/<[^>]+>/g, '')
    .replace(/\s+/g, ' ');
}

describe('HomeCampaigns stats', () => {
  it('agrees the noun with one', () => {
    expect(text('it', campaign(1, 1))).toContain('1 inviato');
    expect(text('it', campaign(1, 1))).toContain('1 risposta');
    expect(text('en', campaign(1, 1))).toContain('1 reply');
  });

  it('uses the plural for any other count', () => {
    expect(text('it', campaign(0, 2))).toContain('0 inviati');
    expect(text('it', campaign(0, 2))).toContain('2 risposte');
    expect(text('en', campaign(0, 2))).toContain('2 replies');
  });
});
