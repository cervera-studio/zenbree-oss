// @vitest-environment happy-dom
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vite-plus/test';
import { click, render, TestIntl } from '@/app/auth/testing';
import emailFailed from '@/lib/api/fixtures/email-failed.json';
import emailNotFound from '@/lib/api/fixtures/email-not-found.json';
import emailNotSearched from '@/lib/api/fixtures/email-not-searched.json';
import emailRefunded from '@/lib/api/fixtures/email-refunded.json';
import type { ListColumn } from '@/lib/lists/api';
import { fixtureRow } from '@/lib/lists/testing';
import { PhoneRows } from './phone-rows';
import type { RowSelection } from './use-row-selection';

// RowPhoneAction in the sheet calls useRouter, which needs the app router outside Next.
vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: () => {} }) }));

const NO_SELECTION: RowSelection = {
  checked: [],
  has: () => false,
  toggle: () => {},
  all: false,
  toggleAll: () => {},
  clear: () => {},
};

function card(json: object) {
  const row = fixtureRow(json);
  return renderToStaticMarkup(
    <TestIntl>
      <PhoneRows
        listId="l"
        chatHref="/?s=c1"
        columns={[]}
        entity="person"
        rows={[row]}
        specs={[]}
        selection={NO_SELECTION}
        onRate={() => {}}
        onToggleExcluded={() => {}}
        onRowChange={() => {}}
        phoneCredits={8}
      />
    </TestIntl>,
  );
}

describe('phone card email (#521)', () => {
  it('says «Non ancora cercata» on a row never searched, and that a search failed on one that failed', () => {
    expect(card(emailNotSearched)).toContain('Non ancora cercata');
    const html = card(emailFailed);
    expect(html).toContain('Ricerca non riuscita');
    expect(html).not.toContain('Non trovata');
  });

  it('says «Non trovata» with «Da completare» once a search found no email', () => {
    const html = card(emailNotFound);
    expect(html).toContain('Non trovata');
    expect(html).toContain('Da completare');
  });

  it('says «Trovata, credito reso» on a row whose found email a turn refunded (#531)', () => {
    const html = card(emailRefunded);
    expect(html).toContain('Trovata, credito reso');
    expect(html).not.toContain('Non ancora cercata');
  });
});

describe('phone sheet email (#531)', () => {
  const columns: ListColumn[] = [
    { key: 'phone', label: 'Telefono', kind: 'static', position: 0, config: { required: true } },
  ];

  async function sheet(own: Parameters<typeof fixtureRow>[1] = {}) {
    const row = fixtureRow(emailRefunded, own);
    const c = await render(
      <PhoneRows
        listId="l"
        chatHref="/?s=c1"
        columns={columns}
        entity="person"
        rows={[row]}
        specs={[]}
        selection={NO_SELECTION}
        onRate={() => {}}
        onToggleExcluded={() => {}}
        onRowChange={() => {}}
        phoneCredits={8}
      />,
    );
    const card = c.querySelector('button');
    if (!card) throw new Error('no card');
    await click(card);
    return document.querySelector('[role=dialog]');
  }

  it('says the email was refunded and links the draft that asks for it by row', async () => {
    const dialog = await sheet();
    expect(dialog?.textContent).toContain('Trovata, credito reso');
    const link = [...(dialog?.querySelectorAll('a') ?? [])].find(
      (a) => a.textContent === 'Chiedi in chat',
    );
    expect(decodeURIComponent(link?.getAttribute('href') ?? '')).toContain('/?s=c1&draft=');
    expect(decodeURIComponent(link?.getAttribute('href') ?? '')).toContain(
      `Trova di nuovo l'email della riga ${emailRefunded.id} e completa le colonne obbligatorie che le mancano: Telefono.`,
    );
  });

  it('links no draft on an excluded row', async () => {
    const dialog = await sheet({ excluded: true });
    expect(dialog?.textContent).toContain('Trovata, credito reso');
    expect(dialog?.textContent).not.toContain('Chiedi in chat');
  });
});
