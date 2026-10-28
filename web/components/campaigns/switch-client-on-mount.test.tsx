// @vitest-environment happy-dom
import { act } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { render } from '@/app/auth/testing';
import { setCurrentProjectId } from '@/lib/projects';
import { SwitchClientOnMount } from './switch-client-on-mount';

const { toastError } = vi.hoisted(() => ({ toastError: vi.fn() }));
vi.mock('@/components/ui/sonner', () => ({ toast: { error: toastError } }));
vi.mock('@/lib/projects', () => ({ setCurrentProjectId: vi.fn(async () => {}) }));

const ALPHA = '0b0e7c9e-3f43-4d07-8d86-1a1f2f0f6a11';

beforeEach(() => vi.clearAllMocks());

describe('SwitchClientOnMount', () => {
  it("makes the list's client the active one once", async () => {
    await render(<SwitchClientOnMount client={ALPHA} />);
    expect(setCurrentProjectId).toHaveBeenCalledExactlyOnceWith(ALPHA);
    expect(toastError).not.toHaveBeenCalled();
  });

  it('tells the user to reload when the switch fails', async () => {
    vi.mocked(setCurrentProjectId).mockRejectedValueOnce(new Error('offline'));
    await render(<SwitchClientOnMount client={ALPHA} />);
    await act(async () => {});
    expect(toastError).toHaveBeenCalledExactlyOnceWith(
      'Impossibile cambiare cliente. Ricarica la pagina.',
    );
  });
});
