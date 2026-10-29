// @vitest-environment happy-dom
import { NextIntlClientProvider } from 'next-intl';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { FORMATS } from '@/i18n/config';
import { MESSAGES } from '@/messages';
import { ProjectSwitcher } from './project-switcher';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { toastError, loadProjects, isLeaving } = vi.hoisted(() => ({
  toastError: vi.fn(),
  loadProjects: vi.fn(),
  isLeaving: vi.fn(),
}));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/components/ui/sonner', () => ({ toast: { error: toastError } }));
vi.mock('@/lib/projects/load', () => ({ loadProjects, isLeaving, useLoadedProjects: () => null }));
vi.mock('@/lib/projects', () => ({
  projectsApi: {},
  getCurrentProjectId: () => null,
  setCurrentProjectId: vi.fn(),
  useCurrentProjectId: () => null,
}));

const failed = { success: false, error: { code: 'INTERNAL.SERVICE_UNAVAILABLE', status: 503 } };

async function mount() {
  const root = createRoot(document.createElement('div'));
  await act(async () => {
    root.render(
      <NextIntlClientProvider locale="it" messages={MESSAGES.it} formats={FORMATS}>
        <ProjectSwitcher userId="u1" />
      </NextIntlClientProvider>,
    );
  });
  root.unmount();
}

describe('ProjectSwitcher load failure', () => {
  beforeEach(() => {
    toastError.mockReset();
    loadProjects.mockResolvedValue(failed);
  });

  it('toasts when the load fails on a live page', async () => {
    isLeaving.mockReturnValue(false);
    await mount();
    expect(toastError).toHaveBeenCalledTimes(1);
  });

  it('shows no toast when the page is unloading', async () => {
    isLeaving.mockReturnValue(true);
    await mount();
    expect(toastError).not.toHaveBeenCalled();
  });
});
