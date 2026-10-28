// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vite-plus/test';

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('@/components/ui/sonner', () => ({ toast }));

import { AppError, fail, succeed } from '@/lib/types';
import { useAction } from './use-action';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

type Api = ReturnType<typeof useAction>;

function mount() {
  const ref: { current: Api | null } = { current: null };
  function Probe() {
    ref.current = useAction();
    return null;
  }
  const root = createRoot(document.createElement('div'));
  act(() => root.render(<Probe />));
  const api = () => {
    if (!ref.current) throw new Error('not mounted');
    return ref.current;
  };
  return { api, unmount: () => act(() => root.unmount()) };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

afterEach(() => vi.clearAllMocks());

describe('useAction', () => {
  it('is pending until the action settles, then passes the data to onSuccess', async () => {
    const { api, unmount } = mount();
    const gate = deferred<ReturnType<typeof succeed<number>>>();
    const onSuccess = vi.fn();
    const onSettled = vi.fn();

    act(() => {
      void api().run(() => gate.promise, { onSuccess, onSettled, successToast: 'saved' });
    });
    expect(api().pending).toBe(true);
    expect(onSuccess).not.toHaveBeenCalled();

    await act(async () => gate.resolve(succeed(42)));
    expect(api().pending).toBe(false);
    expect(api().error).toBeNull();
    expect(onSuccess).toHaveBeenCalledWith(42);
    expect(onSettled).toHaveBeenCalledTimes(1);
    expect(toast.success).toHaveBeenCalledWith('saved');
    unmount();
  });

  it('keeps the failure in error, toasts it and skips onSuccess', async () => {
    const { api, unmount } = mount();
    const onSuccess = vi.fn();
    const onError = vi.fn();
    const failure = fail<number>(new AppError('CAMPAIGN.INVALID_STEPS', 'bad step', 422));

    await act(async () => {
      await api().run(async () => failure, {
        onSuccess,
        onError,
        errorToast: (e) => `failed: ${e.message}`,
      });
    });

    expect(api().error?.code).toBe('CAMPAIGN.INVALID_STEPS');
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ message: 'bad step' }));
    expect(onSuccess).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith('failed: bad step');
    expect(toast.success).not.toHaveBeenCalled();
    unmount();
  });

  it('clears the previous error when the next run starts', async () => {
    const { api, unmount } = mount();
    await act(async () => {
      await api().run(async () => fail(new AppError('X', 'no')));
    });
    expect(api().error).not.toBeNull();

    const gate = deferred<ReturnType<typeof succeed<null>>>();
    act(() => {
      void api().run(() => gate.promise);
    });
    expect(api().error).toBeNull();
    await act(async () => gate.resolve(succeed(null)));
    unmount();
  });

  it('treats an undefined result (a redirect) as neither success nor failure', async () => {
    const { api, unmount } = mount();
    const onSuccess = vi.fn();
    await act(async () => {
      await api().run(async () => undefined, { onSuccess });
    });
    expect(api().error).toBeNull();
    expect(onSuccess).not.toHaveBeenCalled();
    unmount();
  });
});
