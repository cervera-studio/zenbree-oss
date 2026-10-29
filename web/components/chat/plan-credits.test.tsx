import { NextIntlClientProvider } from 'next-intl';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vite-plus/test';
import { FORMATS } from '@/i18n/config';
import { smallestPack, type TopUpPack } from '@/lib/chat/plan';
import type { AgentPlan } from '@/lib/chat/types';
import { MESSAGES } from '@/messages';
import { ShortfallFixes } from './plan-credits';

const plan = {
  targetRows: 50,
  stages: [],
  creditsFixed: 3,
  creditsPerRow: 20,
  largeRunCredits: 100,
} as unknown as AgentPlan;

function render(topUpPack: TopUpPack | null) {
  return renderToStaticMarkup(
    <NextIntlClientProvider locale="it" messages={MESSAGES.it} formats={FORMATS}>
      <ShortfallFixes
        plan={plan}
        rows={50}
        valid
        dropped={new Set()}
        balance={942}
        topUpPack={topUpPack}
        onDrop={() => {}}
        onRows={() => {}}
      />
    </NextIntlClientProvider>,
  );
}

describe('«Ricarica crediti» on a plan the balance cannot cover (#260)', () => {
  it('names the smallest pack', () => {
    const pack = smallestPack([
      { credits: 5000, priceEurCents: 7900 },
      { credits: 1000, priceEurCents: 1900 },
    ]);
    expect(pack).toEqual({ credits: 1000, priceEurCents: 1900 });
    expect(render(pack)).toContain('1.000 crediti a 19 €');
  });

  it('keeps the plain copy without packs', () => {
    expect(smallestPack([])).toBeNull();
    expect(render(null)).toContain('Il piano resta qui e lo approvi al ritorno.');
  });
});
