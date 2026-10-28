// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vite-plus/test';
import { render } from '@/app/auth/testing';
import type { SavedView } from '@/lib/views/api';
import { ViewsManager } from './views-manager';

vi.mock('@/app/(app)/views/actions', () => ({
  createViewAction: vi.fn(),
  deleteViewAction: vi.fn(),
  renameViewAction: vi.fn(),
}));

const view = (id: string, name: string, minFit: number | null): SavedView => ({
  id,
  name,
  listId: 'l1',
  listName: 'Agenzie',
  tab: 'all',
  query: '',
  minFit,
  createdAt: '2026-10-22T09:00:00Z',
});

describe('ViewsManager filters', () => {
  it('names the fit floor of a view that has one, and only of that view', async () => {
    const c = await render(
      <ViewsManager views={[view('a', 'Calde', 80), view('b', 'Fredde', null)]} />,
    );
    const filters = (name: string) =>
      [...c.querySelectorAll('tr')].find((r) => r.textContent?.includes(name))?.textContent;
    expect(filters('Calde')).toContain('fit oltre 80');
    expect(filters('Fredde')).not.toContain('fit oltre');
    expect(c.querySelector('a[href="/lists/l1?fit=80"]')).not.toBeNull();
  });
});
