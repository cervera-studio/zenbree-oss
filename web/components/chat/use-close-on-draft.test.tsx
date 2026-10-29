// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vite-plus/test';
import { useCloseOnDraft } from './use-close-on-draft';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

let params = new URLSearchParams();
vi.mock('next/navigation', () => ({ useSearchParams: () => params }));

function Probe({ onClose }: { onClose: () => void }) {
  useCloseOnDraft(onClose);
  return null;
}

async function follow(wide: boolean) {
  vi.spyOn(window, 'matchMedia').mockReturnValue({ matches: wide } as MediaQueryList);
  params = new URLSearchParams({ s: 'c1' });
  const onClose = vi.fn();
  const root = createRoot(document.createElement('div'));
  await act(async () => root.render(<Probe onClose={onClose} />));
  params = new URLSearchParams({ s: 'c1', draft: 'Trova di nuovo l’email della riga r1.' });
  await act(async () => root.render(<Probe onClose={onClose} />));
  return onClose;
}

async function open(wide: boolean) {
  vi.spyOn(window, 'matchMedia').mockReturnValue({ matches: wide } as MediaQueryList);
  params = new URLSearchParams({ s: 'c1', draft: 'Trova di nuovo l’email della riga r1.' });
  const onClose = vi.fn();
  await act(async () =>
    createRoot(document.createElement('div')).render(<Probe onClose={onClose} />),
  );
  return onClose;
}

describe('useCloseOnDraft', () => {
  it('closes the list panel for a draft already in the URL when the chat mounts', async () => {
    expect(await open(false)).toHaveBeenCalledTimes(1);
  });

  it('keeps the panel for a draft in the URL at mount on a wide screen', async () => {
    expect(await open(true)).not.toHaveBeenCalled();
  });

  it('closes the list panel when a draft arrives while the panel covers the composer', async () => {
    expect(await follow(false)).toHaveBeenCalledTimes(1);
  });

  it('keeps the panel beside the chat on a wide screen, where the composer shows', async () => {
    expect(await follow(true)).not.toHaveBeenCalled();
  });
});
