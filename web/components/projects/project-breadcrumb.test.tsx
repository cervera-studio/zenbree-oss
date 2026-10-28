// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { projectsApi } from '@/lib/projects';
import { loadProjects } from '@/lib/projects/load';
import { ProjectBreadcrumb } from './project-breadcrumb';
import { ProjectsViewer } from './projects-viewer';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const current = vi.hoisted(() => ({ id: 'p2' }));
vi.mock('next/link', () => ({ default: (props: object) => <a {...props} /> }));
vi.mock('@/lib/projects', () => ({
  projectsApi: { list: vi.fn() },
  useCurrentProjectId: () => current.id,
}));
const list = vi.mocked(projectsApi.list);
const loaded = (data: { id: string; name: string }[]) =>
  ({
    success: true,
    data: data.map((p) => ({ ...p, creditsThisMonth: 0, overCap: false })),
  }) as never;

async function mount(userId: string) {
  const host = document.createElement('div');
  const root = createRoot(host);
  await act(async () =>
    root.render(
      <ProjectsViewer userId={userId}>
        <ProjectBreadcrumb crumbs={[{ label: 'Liste' }]} label="Percorso" />
      </ProjectsViewer>,
    ),
  );
  return { path: () => host.querySelector('nav')?.textContent, unmount: () => root.unmount() };
}

describe('ProjectBreadcrumb', () => {
  beforeEach(() => {
    list.mockReset();
    current.id = 'p2';
  });

  it('starts the path with the active client once the list has loaded', async () => {
    list.mockResolvedValue(
      loaded([
        { id: 'p1', name: 'Ritmo Sales' },
        { id: 'p2', name: 'Officina Nove' },
      ]),
    );
    await loadProjects('u1', true);
    const page = await mount('u1');
    expect(page.path()).toBe('Officina Nove/Liste');
    page.unmount();
    current.id = 'p1';
    const next = await mount('u1');
    expect(next.path()).toBe('Ritmo Sales/Liste');
    next.unmount();
  });

  it('follows a rename made in the switcher', async () => {
    list.mockResolvedValueOnce(loaded([{ id: 'p2', name: 'Officina Nove' }]));
    await loadProjects('u2', true);
    const page = await mount('u2');
    list.mockResolvedValueOnce(loaded([{ id: 'p2', name: 'Officina Nove Srl' }]));
    await act(() => loadProjects('u2', true));
    expect(page.path()).toBe('Officina Nove Srl/Liste');
    page.unmount();
  });

  it('drops the client when the same tab changes user, before the new list loads', async () => {
    list.mockResolvedValueOnce(loaded([{ id: 'p2', name: 'Officina Nove' }]));
    await loadProjects('u5', true);
    const host = document.createElement('div');
    const root = createRoot(host);
    const render = (userId: string) =>
      act(async () =>
        root.render(
          <ProjectsViewer userId={userId}>
            <ProjectBreadcrumb crumbs={[{ label: 'Liste' }]} label="Percorso" />
          </ProjectsViewer>,
        ),
      );
    await render('u5');
    expect(host.textContent).toBe('Officina Nove/Liste');
    list.mockReturnValueOnce(new Promise(() => {}));
    void loadProjects('u6');
    await render('u6');
    expect(host.textContent).toBe('Liste');
    root.unmount();
  });

  it('never shows the previous user’s client, even on the first render', async () => {
    list.mockResolvedValueOnce(loaded([{ id: 'p2', name: 'Officina Nove' }]));
    await loadProjects('u3', true);
    const page = await mount('u4');
    expect(page.path()).toBe('Liste');
    page.unmount();
  });
});
