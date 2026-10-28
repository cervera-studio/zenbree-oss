// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vite-plus/test';
import { useCopy } from './use-copy';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

let hook: ReturnType<typeof useCopy>;
function Probe() {
  hook = useCopy();
  return null;
}

function mount() {
  const root = createRoot(document.createElement('div'));
  act(() => root.render(<Probe />));
  return root;
}

function stubClipboard(writeText: (text: string) => Promise<void>) {
  vi.stubGlobal('navigator', { clipboard: { writeText } });
}

afterEach(() => vi.unstubAllGlobals());

describe('useCopy', () => {
  it('writes the text, reports success and marks copied until reset', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    stubClipboard(writeText);
    const root = mount();
    expect(hook.copied).toBe(false);
    let ok = false;
    await act(async () => {
      ok = await hook.copy('sbk_secret');
    });
    expect(ok).toBe(true);
    expect(writeText).toHaveBeenCalledWith('sbk_secret');
    expect(hook.copied).toBe(true);
    act(() => hook.resetCopied());
    expect(hook.copied).toBe(false);
    act(() => root.unmount());
  });

  it('reports failure and stays uncopied when the clipboard rejects', async () => {
    stubClipboard(vi.fn().mockRejectedValue(new Error('denied')));
    const root = mount();
    let ok = true;
    await act(async () => {
      ok = await hook.copy('x');
    });
    expect(ok).toBe(false);
    expect(hook.copied).toBe(false);
    act(() => root.unmount());
  });
});
