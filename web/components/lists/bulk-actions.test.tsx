// @vitest-environment happy-dom
import { act, useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vite-plus/test';
import { click, control, render } from '@/app/auth/testing';
import type { ListColumn, ListRow } from '@/lib/lists/api';
import { bulkApi } from '@/lib/lists/bulk';
import { buildColumnModel } from '@/lib/lists/column-model';
import { visibleRows } from '@/lib/lists/view';
import { fail, succeed } from '@/lib/types';
import { AddResultView, AddToCampaignDialog } from './add-to-campaign-dialog';
import { BulkBar } from './bulk-bar';
import { FitChip } from './row-toolbar';
import { RowsTable } from './rows-table';
import { useRowSelection } from './use-row-selection';

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: () => {}, push: () => {} }) }));
afterEach(() => vi.restoreAllMocks());

const columns: ListColumn[] = [{ key: 'full_name', label: 'Nome', kind: 'static', position: 0 }];
const row = (id: string, name: string, score: number): ListRow => ({
  id,
  seq: 1,
  cells: { full_name: name },
  relevance_score: score,
  excluded: false,
  delivered: true,
  lookups: { email: { state: 'not_searched' }, phone: { state: 'not_searched' } },
});
const ROWS = [
  row('a', 'Giulia Ferri', 92),
  row('b', 'Marco Bellini', 85),
  row('c', 'Luca Conti', 70),
];

/** The list page's wiring: the fit chip filters, the selection counts only the shown rows. */
function Page() {
  const [minFit, setMinFit] = useState<number | null>(null);
  const shown = visibleRows(ROWS, 'all', '', minFit);
  const selection = useRowSelection(shown);
  return (
    <>
      <FitChip minFit={minFit} onChange={setMinFit} />
      <RowsTable
        entity="person"
        columns={columns}
        model={buildColumnModel(columns, 'person')}
        rows={shown}
        selectedId={null}
        onSelect={() => {}}
        selection={selection}
        onRate={() => {}}
        chatHref={null}
        onDeleteColumn={() => {}}
      />
      {selection.checked.length > 0 && (
        <BulkBar
          count={selection.checked.length}
          people
          busy={false}
          phone={false}
          onAdd={() => {}}
          onInstantly={() => {}}
          onMark={() => {}}
          onClear={selection.clear}
        />
      )}
    </>
  );
}

const box = (c: HTMLElement, name: string) => {
  const el = c.querySelector<HTMLElement>(`[role=checkbox][aria-label="${name}"]`);
  if (!el) throw new Error(`no checkbox ${name}`);
  return el;
};
const header = (c: HTMLElement) => box(c, 'Seleziona tutte le righe visibili');
const bar = (c: HTMLElement) => c.querySelector('[role=toolbar]')?.textContent ?? null;

describe('row selection', () => {
  it('checks all, some or none of the shown rows, and a filter hides a checked row from the count', async () => {
    const c = await render(<Page />);
    expect(header(c).getAttribute('aria-checked')).toBe('false');
    expect(bar(c)).toBeNull();

    await click(box(c, 'Seleziona Giulia Ferri'));
    expect(header(c).getAttribute('aria-checked')).toBe('mixed');
    expect(bar(c)).toContain('1 selezionata');

    await click(header(c));
    expect(header(c).getAttribute('aria-checked')).toBe('true');
    expect(bar(c)).toContain('3 selezionate');

    await click(control(c, 'Fit oltre 80'));
    expect(c.textContent).not.toContain('Luca Conti');
    expect(header(c).getAttribute('aria-checked')).toBe('true');
    expect(bar(c)).toContain('2 selezionate');

    await click(header(c));
    expect(bar(c)).toBeNull();

    await click(control(c, 'Fit oltre 80'));
    expect(box(c, 'Seleziona Luca Conti').getAttribute('aria-checked')).toBe('true');
    expect(header(c).getAttribute('aria-checked')).toBe('mixed');
    expect(bar(c)).toContain('1 selezionata');
  });

  it('clears every check from the bar', async () => {
    const c = await render(<Page />);
    await click(header(c));
    await click(control(c, 'Annulla selezione'));
    expect(bar(c)).toBeNull();
    expect(header(c).getAttribute('aria-checked')).toBe('false');
  });
});

describe('AddResultView', () => {
  const campaign = { id: 'c1', name: 'Q4 Milano' };

  it('names the people added and one line per reason that occurred, with its next step', async () => {
    const onShowTab = vi.fn();
    const c = await render(
      <AddResultView
        campaign={campaign}
        result={{ added: 3, skipped: { excluded: 2, not_delivered: 1, other: 4 } }}
        errorText={null}
        onShowTab={onShowTab}
        onClose={() => {}}
      />,
    );
    expect(c.querySelector('strong')?.textContent).toBe('3 persone aggiunte a «Q4 Milano»');
    expect([...c.querySelectorAll('li > span')].map((s) => s.textContent)).toEqual([
      '2 persone sono in Esclusioni: toglile da lì per aggiungerle.',
      '1 riga non è consegnata: è da completare o esclusa dalla lista. Completala o includila prima.',
      '4 persone non sono state aggiunte per un altro motivo.',
    ]);
    expect(control(c, 'Apri Esclusioni').getAttribute('href')).toBe('/settings/exclusions');
    expect(control(c, 'Apri campagna').getAttribute('href')).toBe('/campaigns/c1');
    await click(control(c, 'Mostra Da completare'));
    expect(onShowTab).toHaveBeenCalledWith('pending');
  });

  it('says plainly that no one was added', async () => {
    const c = await render(
      <AddResultView
        campaign={campaign}
        result={{ added: 0, skipped: { duplicate: 1 } }}
        errorText={null}
        onShowTab={() => {}}
        onClose={() => {}}
      />,
    );
    expect(c.querySelector('strong')?.textContent).toBe('Nessuna persona aggiunta a «Q4 Milano»');
    expect(c.querySelector('li')?.textContent).toBe(
      '1 persona è già in questa campagna, o ha già risposto o è già stata contattata.',
    );
  });
});

describe('AddToCampaignDialog', () => {
  it('shows a campaign that started meanwhile as an error and reloads the drafts', async () => {
    const drafts = vi
      .spyOn(bulkApi, 'draftCampaigns')
      .mockResolvedValue(succeed([{ id: 'c1', name: 'Q4 Milano' }]));
    vi.spyOn(bulkApi, 'addToCampaign').mockResolvedValue(
      fail({ name: 'AppError', code: 'CAMPAIGN.NOT_DRAFT', message: 'not a draft', status: 409 }),
    );
    const onAdded = vi.fn();
    await render(
      <AddToCampaignDialog
        listId="l1"
        rowIds={['a', 'b']}
        onClose={() => {}}
        onAdded={onAdded}
        onShowTab={() => {}}
      />,
    );
    const dialog = document.querySelector<HTMLElement>('[role=dialog]');
    if (!dialog) throw new Error('no dialog');
    expect(dialog.textContent).toContain('2 persone selezionate');

    const radio = dialog.querySelector<HTMLInputElement>('input[type=radio]');
    if (!radio) throw new Error('no campaign');
    await act(async () => radio.click());
    await click(control(dialog, 'Aggiungi'));

    expect(dialog.querySelector('[role=alert]')?.textContent).toBe(
      'Questa campagna è già partita: si può modificare solo una campagna in bozza.',
    );
    expect(drafts).toHaveBeenCalledTimes(2);
    expect(onAdded).not.toHaveBeenCalled();
  });

  it('offers to create a campaign when the client has no draft', async () => {
    vi.spyOn(bulkApi, 'draftCampaigns').mockResolvedValue(succeed([]));
    await render(
      <AddToCampaignDialog
        listId="l1"
        rowIds={['a']}
        onClose={() => {}}
        onAdded={() => {}}
        onShowTab={() => {}}
      />,
    );
    const dialog = document.querySelector<HTMLElement>('[role=dialog]');
    if (!dialog) throw new Error('no dialog');
    expect(dialog.textContent).toContain('Nessuna campagna in bozza per questo cliente.');
    expect(control(dialog, 'Crea una campagna').getAttribute('href')).toBe('/campaigns?list=l1');
  });
});
