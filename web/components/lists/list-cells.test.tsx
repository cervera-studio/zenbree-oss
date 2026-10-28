import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vite-plus/test';
import { TestIntl } from '@/app/auth/testing';
import emailFailed from '@/lib/api/fixtures/email-failed.json';
import emailFound from '@/lib/api/fixtures/email-found.json';
import emailNotFound from '@/lib/api/fixtures/email-not-found.json';
import emailNotSearched from '@/lib/api/fixtures/email-not-searched.json';
import emailRefunded from '@/lib/api/fixtures/email-refunded.json';
import emailSearching from '@/lib/api/fixtures/email-searching.json';
import { rowSchema } from '@/lib/lists/api';
import { fixtureRow, lookups } from '@/lib/lists/testing';
import { Cell } from './list-cells';

const spec = { key: 'email', label: 'Email', render: 'personEmail' } as const;

function render(json: object, excluded = false) {
  return renderToStaticMarkup(
    <TestIntl>
      <Cell spec={spec} row={fixtureRow(json, { excluded })} chatHref="/?s=c1" columns={[]} />
    </TestIntl>,
  );
}

describe('email Cell', () => {
  it('pulses «In ricerca» on a row the API reports as searching, only where motion is fine', () => {
    const html = render(emailSearching);
    expect(html).toContain('In ricerca');
    expect(html).toContain('motion-safe:animate-pulse');
    expect(html).not.toMatch(/(?<!:)animate-pulse/);
  });

  it('does not pulse a row that no running turn searches, whatever else is going on', () => {
    for (const row of [emailNotSearched, emailFailed, emailNotFound, emailFound]) {
      const html = render(row);
      expect(html).not.toContain('In ricerca');
      expect(html).not.toContain('animate-pulse');
    }
  });

  it('reads an answered row with its address or «Non trovata» and its chip', () => {
    const miss = render(emailNotFound);
    expect(miss).toContain('Non trovata');
    expect(miss).toContain('Da completare');
    const found = render(emailFound);
    expect(found).toContain('mailto:anna@acme.it');
    expect(found).toContain('Verificata');
  });

  it('reads «Non ancora cercata» with no chip on a row never searched, excluded or not', () => {
    for (const html of [render(emailNotSearched), render(emailNotSearched, true)]) {
      expect(html).toContain('Non ancora cercata');
      expect(html).not.toContain('Da completare');
    }
  });

  it('reads a refunded email as on the list page, with «Chiedi in chat» drafting in the same chat', () => {
    const html = render(emailRefunded);
    expect(html).toContain('Trovata, credito reso');
    expect(html).toContain('Chiedi in chat');
    expect(html).toContain('href="/?s=c1&amp;draft=');
    expect(html).not.toContain('In ricerca');
  });
});

describe('AI Cell', () => {
  const ai = { key: 'ai_hubspot', label: 'Usa HubSpot', render: 'ai' } as const;
  const aiRow = (status: string) =>
    rowSchema.parse({
      id: 'r1',
      seq: 1,
      cells: {},
      relevance_score: null,
      excluded: false,
      lookups: lookups(),
      cell_meta: { ai_hubspot: { status } },
    });
  const renderAi = (status: string) =>
    renderToStaticMarkup(
      <TestIntl>
        <Cell spec={ai} row={aiRow(status)} chatHref={null} columns={[]} />
      </TestIntl>,
    );

  it('marks a failed cell in red with a short visible word, unlike a cell with no answer', () => {
    const failed = renderAi('failed');
    expect(failed).toContain('text-negative');
    expect(failed).toContain('Non riuscita');
    expect(failed).toContain('Ricerca non riuscita: compila di nuovo per riprovare');
    const empty = renderAi('empty');
    expect(empty).toContain('—');
    expect(empty).not.toContain('text-negative');
    expect(empty).not.toContain('Non riuscita');
  });
});
