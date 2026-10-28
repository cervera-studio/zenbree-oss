// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vite-plus/test';
import { useNavState } from './nav';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

vi.mock('@/lib/projects', () => ({ useCurrentProjectId: () => 'p1' }));
// Stable, as Next's router is: the hook resubscribes when it changes.
const router = vi.hoisted(() => ({ refresh: () => {} }));
vi.mock('next/navigation', () => ({ useRouter: () => router }));
vi.mock('@/lib/api/event-stream', () => ({
  followStream: async (_path: string, _cursor: string | null, onFrame: (f: object) => void) => {
    onFrame({ event: 'error', id: '', data: '{"code":"INTERNAL.UNEXPECTED"}' });
  },
}));

const seen: { failed: boolean }[] = [];

function Probe() {
  seen.push(useNavState());
  return null;
}

describe('useNavState', () => {
  it('marks the nav failed when its stream ends with an error frame, so no skeleton waits for good', async () => {
    const root = createRoot(document.createElement('div'));
    await act(async () => root.render(<Probe />));
    expect(seen.at(-1)).toEqual({ nav: null, failed: true });
    act(() => root.unmount());
  });
});
