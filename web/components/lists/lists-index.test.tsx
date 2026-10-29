// @vitest-environment happy-dom
import { NextIntlClientProvider } from 'next-intl';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { FORMATS } from '@/i18n/config';
import { MESSAGES } from '@/messages';
import { ListsIndex } from './lists-index';

const { all, remove, restore, success, error } = vi.hoisted(() => ({
  all: vi.fn(),
  remove: vi.fn(),
  restore: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}));
vi.mock('@/lib/lists/api', () => ({ listsApi: { all, remove, restore } }));
vi.mock('@/lib/projects', () => ({ useCurrentProjectId: () => 'p1' }));
vi.mock('@/components/ui/sonner', () => ({ toast: { success, error } }));
vi.mock('./csv-import-dialog', () => ({ CsvImportDialog: () => null }));

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
beforeEach(() => vi.clearAllMocks());

const list = (id: string, name: string, created_at: string) => ({
  id,
  name,
  entity: 'person',
  mode: 'build',
  chat_session_id: null,
  project_id: 'p1',
  created_at,
  row_count: 3,
  delivered_count: 3,
  can_expand: false,
  status: 'ready',
  target_rows: null,
  credits_used: 0,
  brief: null,
  chat_title: null,
});

async function render() {
  all.mockResolvedValue({
    success: true,
    data: {
      lists: [
        list('l2', 'Seconda', '2026-10-07T10:00:00Z'),
        list('l1', 'Prima', '2026-10-06T10:00:00Z'),
      ],
    },
  });
  const container = document.createElement('div');
  document.body.replaceChildren(container);
  await act(async () => {
    createRoot(container).render(
      <NextIntlClientProvider locale="it" messages={MESSAGES.it} formats={FORMATS}>
        <ListsIndex />
      </NextIntlClientProvider>,
    );
  });
  return container.querySelector('table') as HTMLTableElement;
}

const names = (table: HTMLTableElement) =>
  [...table.querySelectorAll('tbody tr')].map((r) => r.querySelector('a')?.textContent);

// Delete sits behind the row's «…» menu, not on the row itself.
async function clickDelete(table: HTMLTableElement, name: string) {
  expect(table.querySelector('button[aria-label^="Elimina"]')).toBeNull();
  const menu = table.querySelector(`button[aria-label="Azioni per «${name}»"]`) as HTMLElement;
  await act(async () =>
    menu.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 })),
  );
  const item = [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find(
    (e) => e.textContent?.trim() === 'Elimina',
  ) as HTMLElement;
  await act(async () => item.click());
}

describe('ListsIndex delete', () => {
  it('drops the list at once and puts it back in place on Annulla', async () => {
    remove.mockResolvedValue({ success: true });
    restore.mockResolvedValue({ success: true });
    const table = await render();
    await clickDelete(table, 'Seconda');
    expect(remove).toHaveBeenCalledWith('l2');
    expect(names(table)).toEqual(['Prima']);
    const [message, { action }] = success.mock.calls[0] as [
      string,
      { action: { label: string; onClick: () => Promise<void> } },
    ];
    expect([message, action.label]).toEqual(['Lista eliminata', 'Annulla']);
    await act(async () => action.onClick());
    expect(restore).toHaveBeenCalledWith('l2');
    expect(names(table)).toEqual(['Seconda', 'Prima']);
  });

  it('keeps a list the API refuses to delete', async () => {
    remove.mockResolvedValue({ success: false, error: { code: 'LIST.BUSY' } });
    const table = await render();
    await clickDelete(table, 'Prima');
    expect(names(table)).toEqual(['Seconda', 'Prima']);
    expect(error).toHaveBeenCalledWith('Aspetta che la lista finisca di riempirsi.');
    expect(success).not.toHaveBeenCalled();
  });

  it('names the owner rule when a member deletes a teammate list', async () => {
    remove.mockResolvedValue({ success: false, error: { code: 'LIST.NOT_OWNER' } });
    const table = await render();
    await clickDelete(table, 'Prima');
    expect(names(table)).toEqual(['Seconda', 'Prima']);
    expect(error).toHaveBeenCalledWith(MESSAGES.it.errors.LIST.NOT_OWNER);
  });
});
