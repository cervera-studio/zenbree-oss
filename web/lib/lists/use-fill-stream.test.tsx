// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vite-plus/test';
import type { List } from './api';
import { useFillStream } from './fill-stream';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const NOT_A_MEMBER = '{"message":"gone","code":"AUTH.NOT_A_MEMBER"}';

vi.mock('@/lib/api/event-stream', async (real) => ({
  ...(await real<typeof import('@/lib/api/event-stream')>()),
  followStream: async (_path: string, _cursor: string | null, onFrame: (f: object) => void) => {
    onFrame({ event: 'error', id: '', data: NOT_A_MEMBER });
  },
}));

const filling = {
  id: 'l1',
  fill_cursor: '1-0',
  columns: [
    { key: 'ai_h', label: 'H', kind: 'ai', position: 1, config: { fill: { status: 'running' } } },
  ],
} as unknown as List;

function Probe({ onStreamError }: { onStreamError: (code: string) => void }) {
  useFillStream<List>(
    filling,
    (_: unknown) => {},
    (_: unknown) => {},
    undefined,
    onStreamError,
  );
  return null;
}

describe('useFillStream', () => {
  it('reports the code of an error frame, so the list stops showing its fill as running', async () => {
    const onStreamError = vi.fn();
    const root = createRoot(document.createElement('div'));
    await act(async () => root.render(<Probe onStreamError={onStreamError} />));
    expect(onStreamError).toHaveBeenCalledExactlyOnceWith('AUTH.NOT_A_MEMBER');
    act(() => root.unmount());
  });
});
