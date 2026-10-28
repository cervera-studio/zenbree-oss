// @vitest-environment happy-dom
import { act } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { createViewAction } from '@/app/(app)/views/actions';
import { click, field, render } from '@/app/auth/testing';
import { SaveViewDialog } from './save-view-dialog';

vi.mock('@/app/(app)/views/actions', () => ({ createViewAction: vi.fn() }));
beforeEach(() => {
  vi.mocked(createViewAction)
    .mockReset()
    .mockResolvedValue({
      success: true,
      data: {
        id: 'v1',
        name: 'Calde',
        listId: 'l1',
        listName: 'Agenzie',
        tab: 'delivered',
        query: 'milano',
        minFit: 80,
        createdAt: '2026-10-22T09:00:00Z',
      },
    });
});

const button = (text: string) => {
  const found = [...document.body.querySelectorAll('button')].find(
    (b) => b.textContent?.trim() === text,
  );
  if (!found) throw new Error(`no button ${text}`);
  return found;
};

async function save(minFit: number | null) {
  await render(<SaveViewDialog listId="l1" tab="delivered" query=" milano " minFit={minFit} />);
  await click(button('Salva vista'));
  const name = field(document.body, 'Nome');
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(name, 'Calde');
    name.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await click(button('Salva'));
}

describe('SaveViewDialog', () => {
  it('saves the active fit floor with the tab and the search', async () => {
    await save(80);
    expect(createViewAction).toHaveBeenCalledWith({
      listId: 'l1',
      name: 'Calde',
      tab: 'delivered',
      query: 'milano',
      minFit: 80,
    });
  });

  it('saves no fit floor when the fit chip is off', async () => {
    await save(null);
    expect(createViewAction).toHaveBeenCalledWith(expect.objectContaining({ minFit: null }));
  });
});
