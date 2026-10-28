// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vite-plus/test';
import { useEscape } from './use-escape';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

function Probe({ onEscape }: { onEscape: () => void }) {
  useEscape(onEscape);
  return null;
}

function mount(onEscape: () => void) {
  const root = createRoot(document.createElement('div'));
  act(() => root.render(<Probe onEscape={onEscape} />));
  return root;
}

function press(key: string, prevent = false) {
  const event = new KeyboardEvent('keydown', { key, cancelable: true });
  if (prevent) event.preventDefault();
  document.dispatchEvent(event);
}

describe('useEscape', () => {
  it('calls the handler on Escape only', () => {
    const onEscape = vi.fn();
    const root = mount(onEscape);
    press('a');
    expect(onEscape).not.toHaveBeenCalled();
    press('Escape');
    expect(onEscape).toHaveBeenCalledTimes(1);
    act(() => root.unmount());
  });

  it('ignores an Escape that another handler already consumed', () => {
    const onEscape = vi.fn();
    const consume = (e: KeyboardEvent) => e.preventDefault();
    document.addEventListener('keydown', consume);
    const root = mount(onEscape);
    press('Escape');
    document.removeEventListener('keydown', consume);
    expect(onEscape).not.toHaveBeenCalled();
    act(() => root.unmount());
  });

  it('removes the listener on unmount', () => {
    const onEscape = vi.fn();
    const root = mount(onEscape);
    act(() => root.unmount());
    press('Escape');
    expect(onEscape).not.toHaveBeenCalled();
  });
});
