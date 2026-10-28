import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vite-plus/test';
import { TestIntl } from '@/app/auth/testing';
import emailCatchAll from '@/lib/api/fixtures/email-catch-all.json';
import emailNotFound from '@/lib/api/fixtures/email-not-found.json';
import phoneFound from '@/lib/api/fixtures/phone-found.json';
import phoneNotFound from '@/lib/api/fixtures/phone-not-found.json';
import { type ListColumn, type ListRow, rowSchema } from '@/lib/lists/api';
import { type Entity, panelColumnSpecs } from '@/lib/lists/column-model';
import { fixtureRow, lookups, strayStatus } from '@/lib/lists/testing';
import { ListTable } from './list-table';

describe('the chat panel table', () => {
  const column = (key: string, label: string, position: number): ListColumn => ({
    key,
    label,
    kind: 'static',
    position,
  });
  const columns = [
    column('full_name', 'Full name', 0),
    column('phone', 'Phone', 1),
    column('phone_status', 'Phone status', 2),
  ];
  /** A company row: the API runs no email or phone search on one. */
  const company = (id: string, cells: Record<string, unknown>) =>
    rowSchema.parse({
      id,
      seq: 1,
      cells,
      relevance_score: null,
      excluded: false,
      lookups: lookups(),
    });

  const render = (cols: ListColumn[], rows: ListRow[], entity: Entity = 'person') =>
    renderToStaticMarkup(
      <TestIntl>
        <ListTable
          specs={panelColumnSpecs(cols, entity, (key) =>
            key === 'column.email' ? 'Email' : 'Telefono',
          )}
          rows={rows}
          onToggleExcluded={() => {}}
          chatHref="/?s=c1"
          columns={cols}
        />
      </TestIntl>,
    );

  it('reads «Non trovato» after a phone search that found nothing, with no raw status column', () => {
    const html = render(columns, [fixtureRow(phoneFound.row), fixtureRow(phoneNotFound.row)]);
    expect(html).toContain('+39333111222');
    expect(html).toContain('Non trovato');
    expect(html).not.toContain('Phone status');
    expect(html).not.toContain(strayStatus('phone_status'));
  });

  it('reads «Non trovato» on a list whose only phone column is the status: a row lookup stores no empty phone', () => {
    const html = render(
      [column('full_name', 'Full name', 0), column('phone_status', 'Phone status', 1)],
      [fixtureRow(phoneNotFound.row)],
    );
    expect(html).toContain('Non trovato');
    expect(html).not.toContain(strayStatus('phone_status'));
  });

  it('folds the email status into the Email column, as the list page does (#525)', () => {
    const html = render(
      [
        column('full_name', 'Full name', 0),
        column('email', 'Email', 1),
        column('email_status', 'Email status', 2),
      ],
      [fixtureRow(emailCatchAll), fixtureRow(emailNotFound)],
    );
    expect(html).not.toContain('Email status');
    expect(html).not.toContain(strayStatus('email_status'));
    expect(html).toContain('mailto:elsa@acme.it');
    expect(html).toContain('Catch-all');
    expect(html).toContain('Non trovata');
    expect(html).toContain('Da completare');
  });

  it("shows a company list's own email column as plain text: no email search runs there", () => {
    const html = render(
      [column('name', 'Name', 0), column('email', 'Email', 1)],
      [company('a', { name: 'Acme', email: 'info@acme.it' }), company('b', { name: 'Bo Srl' })],
      'company',
    );
    expect(html).toContain('mailto:info@acme.it');
    expect(html).not.toContain('Da verificare');
    expect(html).not.toContain('Non ancora cercata');
  });
});
