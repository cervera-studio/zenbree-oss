// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vite-plus/test';
import { render } from '@/app/auth/testing';
import { NotFoundView } from './not-found-view';

const { all, project } = vi.hoisted(() => ({
  all: vi.fn(),
  project: { id: 'p1' as string | null },
}));
vi.mock('next/navigation', () => ({ usePathname: () => '/lists/gone' }));
vi.mock('@/lib/projects', () => ({ useCurrentProjectId: () => project.id }));
vi.mock('@/lib/lists/api', () => ({ listsApi: { all } }));
afterEach(() => {
  project.id = 'p1';
});

const list = (id: string, name: string, entity: string, delivered_count: number) => ({
  id,
  name,
  entity,
  delivered_count,
});

describe('NotFoundView «Forse cercavi»', () => {
  it('offers the two newest lists', async () => {
    all.mockResolvedValue({
      success: true,
      data: {
        lists: [
          list('a', 'Agenzie · SDR', 'person', 47),
          list('b', 'Agenzie · aziende', 'company', 31),
          list('c', 'Vecchia', 'person', 3),
        ],
      },
    });
    const c = await render(<NotFoundView />);
    const links = [...c.querySelectorAll('nav a')].map((a) => [
      a.textContent,
      a.getAttribute('href'),
    ]);
    expect(links).toEqual([
      ['Agenzie · SDR · 47 persone', '/lists/a'],
      ['Agenzie · aziende · 31 aziende', '/lists/b'],
    ]);
  });

  it('keeps the fixed destinations without a project', async () => {
    project.id = null;
    const c = await render(<NotFoundView />);
    expect([...c.querySelectorAll('nav a')].map((a) => a.textContent)).toEqual(['Le tue campagne']);
  });
});
