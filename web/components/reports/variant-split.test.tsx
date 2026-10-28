import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vite-plus/test';
import { TestIntl } from '@/app/auth/testing';
import type { ReportVariant } from '@/lib/reports/api';
import { VariantSplit } from './variant-split';

const variant = (extra: Partial<ReportVariant>): ReportVariant => ({
  group: 'a',
  enrolled: 1200,
  contacted: 40,
  sent: { email: 55, linkedinConnect: 0, linkedinMessage: 0 },
  humanReplies: 4,
  positiveReplies: 1,
  bounced: null,
  ...extra,
});

const split = (variants: ReportVariant[]) =>
  renderToStaticMarkup(
    <TestIntl>
      <VariantSplit variants={variants} />
    </TestIntl>,
  )
    .replace(/<[^>]+>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/\s+/g, ' ');

describe('variant split', () => {
  it('shows counts with their denominators and never a percentage', () => {
    const html = split([variant({})]);
    expect(html).toContain('Persone contattate 40');
    expect(html).toContain('Risposte 4 su 40 persone contattate');
    expect(html).toContain('Risposte positive 1 su 4 risposte');
    expect(html).not.toContain('%');
  });

  it('shows plain counts for the replies no person contacted can explain', () => {
    const html = split([
      variant({}),
      variant({ group: 'unknown', enrolled: 0, contacted: 0, humanReplies: 1, positiveReplies: 1 }),
    ]);
    const unknown = html.slice(html.indexOf('Non attribuite'));
    expect(unknown).toContain('Risposte 1 Risposte positive 1');
    expect(unknown).not.toContain(' su ');
    expect(html).toContain('Risposte 4 su 40 persone contattate');
  });

  it('splits the sends by channel only when LinkedIn sent something', () => {
    expect(split([variant({})])).toContain('Messaggi inviati 55');
    const linkedin = variant({ sent: { email: 3, linkedinConnect: 1, linkedinMessage: 2 } });
    expect(split([linkedin])).toContain('Messaggi inviati 6 (3 email · 1 invito · 2 messaggi)');
  });
});
