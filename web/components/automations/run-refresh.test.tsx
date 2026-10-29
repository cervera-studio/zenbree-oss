// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vite-plus/test';
import { RunRefresh } from './run-refresh';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
const refresh = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh }) }));

afterEach(() => vi.useRealTimers());

it('refreshes the page every five seconds until the page drops it', async () => {
  vi.useFakeTimers();
  const root = createRoot(document.createElement('div'));
  await act(async () => root.render(<RunRefresh />));
  await act(async () => vi.advanceTimersByTime(10_000));
  expect(refresh).toHaveBeenCalledTimes(2);
  await act(async () => root.unmount());
  vi.advanceTimersByTime(10_000);
  expect(refresh).toHaveBeenCalledTimes(2);
});
