import { NextIntlClientProvider } from 'next-intl';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vite-plus/test';
import { FORMATS } from '@/i18n/config';
import type { Funnel } from '@/lib/chat/types';
import { MESSAGES } from '@/messages';
import { NoResultsCard } from './no-results-card';

const funnel: Funnel = {
  steps: [
    { group: 'where', values: ['Piemonte, Italy'], count: 56557 },
    { group: 'size', values: ['20–100'], count: 23 },
    { group: 'signal', values: ['Account Executive'], count: 0 },
  ],
  excludedIndex: 2,
  entity: 'company',
};
const question = {
  question: 'No company matches every filter',
  options: [
    {
      label: 'Without signal',
      description: null,
      drop: 'signal' as const,
      estimate: { count: 7, noun: 'company' as const },
    },
  ],
  funnel,
};

function render(props: Partial<Parameters<typeof NoResultsCard>[0]>) {
  return renderToStaticMarkup(
    <NextIntlClientProvider locale="it" messages={MESSAGES.it} formats={FORMATS}>
      <NoResultsCard question={question} funnel={funnel} creditsUsed={0} {...props} />
    </NextIntlClientProvider>,
  );
}

describe('NoResultsCard', () => {
  it('lists every step with its formatted count and marks only the excluding one', () => {
    const html = render({});
    expect(html).toContain('Con sede a Piemonte, Italy');
    expect(html).toContain('20–100 dipendenti');
    expect(html).toContain('Con annunci aperti per Account Executive');
    expect(html).toContain('>56.557<');
    expect(html.match(/aria-current="step"/g)).toHaveLength(1);
    expect(html.indexOf('aria-current="step"')).toBeGreaterThan(html.indexOf('20–100'));
  });

  it('draws a valid bar when the first step already left nothing', () => {
    const empty: Funnel = {
      steps: [{ group: 'where', values: ['Atlantide'], count: 0 }],
      excludedIndex: 0,
      entity: 'company',
    };
    expect(render({ funnel: empty })).not.toContain('NaN');
  });

  it('says nothing was charged only when nothing was, without claiming that no list exists', () => {
    expect(render({ creditsUsed: 0 })).toContain('non ti ho addebitato nulla');
    expect(render({ creditsUsed: 2 })).not.toContain('non ti ho addebitato nulla');
    expect(render({})).not.toContain('Non ho creato la lista');
  });

  it('words each alternative in the interface language, with its estimate, only while open', () => {
    const open = render({ onAnswer: () => {} });
    expect(open).toContain('Senza il segnale');
    expect(open).toContain('~7 aziende');
    expect(render({})).not.toContain('~7 aziende');
  });

  it('words a people funnel as people, with one prose line per step (#260)', () => {
    const people: Funnel = {
      steps: [
        { group: 'where', values: ['Torino', 'Milano'], count: 900 },
        { group: 'who', values: ['Head of Sales'], count: 0 },
      ],
      excludedIndex: 1,
      entity: 'person',
    };
    const html = render({ funnel: people });
    expect(html).toContain('Nessuna persona rispetta tutti i filtri');
    expect(html).toContain('Che lavorano a Torino o Milano');
    expect(html).toContain('Con ruolo Head of Sales');
  });
});
