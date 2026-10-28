import { NextIntlClientProvider } from 'next-intl';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vite-plus/test';
import { FORMATS } from '@/i18n/config';
import type { Segment } from '@/lib/onboarding/schema';
import { MESSAGES } from '@/messages';
import { SegmentsStep } from './step-segments';

const segment = (name: string, signalHint?: string): Segment => ({
  name,
  who: 'CTO',
  problem: 'p',
  outcome: 'o',
  criteria: {
    titles: [],
    seniorities: [],
    industries: [],
    employeeRanges: [],
    locations: [],
    keywords: [],
  },
  ...(signalHint === undefined ? {} : { signalHint }),
});

function text(locale: 'en' | 'it', segments: Segment[]) {
  const noop = () => {};
  const html = renderToStaticMarkup(
    <NextIntlClientProvider locale={locale} messages={MESSAGES[locale]} formats={FORMATS}>
      <SegmentsStep
        segments={segments}
        busy={null}
        error={null}
        onBack={noop}
        onRegenerate={noop}
        onPick={noop}
        onSkip={noop}
      />
    </NextIntlClientProvider>,
  );
  return html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
}

describe('SegmentsStep signal hint', () => {
  it('labels the hint as something to look for, in the page language', () => {
    expect(text('en', [segment('A', 'open job postings')])).toContain(
      'Look for: open job postings',
    );
    expect(text('it', [segment('A', 'annunci di lavoro')])).toContain(
      'Da cercare: annunci di lavoro',
    );
  });

  it('shows no hint for an empty one or a segment stored before it', () => {
    const page = text('en', [segment('Agencies', ''), segment('Banks')]);
    expect(page).toContain('Agencies');
    expect(page).toContain('Banks');
    expect(page).not.toContain('Look for');
  });
});

describe('SegmentsStep countries (demo QA auth-3)', () => {
  it('names the countries in the page language', () => {
    const s = segment('A');
    s.criteria.locations = ['Italy', 'Spain', 'Europe', 'Rome, Italy'];
    expect(text('it', [s])).toContain('Paesi Italia, Spagna, Europe, Rome, Italy');
  });
});
