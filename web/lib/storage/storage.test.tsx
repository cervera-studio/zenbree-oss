// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToString } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { getStored, removeStored, setStored } from './storage';
import { useStored } from './use-stored';

function Probe({ area = 'local' }: { area?: 'local' | 'session' }) {
  return <p>{useStored('k', area) ?? 'none'}</p>;
}

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

function mount() {
  const el = document.createElement('div');
  const root = createRoot(el);
  act(() => root.render(<Probe />));
  return { text: () => el.textContent, unmount: () => act(() => root.unmount()) };
}

// A stubbed global, not a spy on Storage.prototype: happy-dom's storage keeps throwing after the spy is restored,
// which blocked storage for every later test in the file.
function blockStorage() {
  const blocked = () => {
    throw new Error('blocked');
  };
  const storage = { getItem: blocked, setItem: blocked, removeItem: blocked };
  vi.stubGlobal('localStorage', storage);
  vi.stubGlobal('sessionStorage', storage);
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});
afterEach(() => {
  vi.unstubAllGlobals();
  removeStored('k', 'local');
  removeStored('k', 'session');
});

describe('storage', () => {
  it('round-trips a value per area and removes it', () => {
    setStored('k', 'a', 'local');
    setStored('k', 'b', 'session');
    expect(getStored('k', 'local')).toBe('a');
    expect(getStored('k', 'session')).toBe('b');
    expect(localStorage.getItem('k')).toBe('a');
    removeStored('k', 'local');
    expect(getStored('k', 'local')).toBeNull();
    expect(getStored('k', 'session')).toBe('b');
  });

  it('reads null and does not throw when storage throws', () => {
    blockStorage();
    expect(getStored('k', 'local')).toBeNull();
    expect(() => removeStored('k', 'local')).not.toThrow();
  });

  it('keeps a refused write readable for the page session', () => {
    blockStorage();
    setStored('k', '1', 'local');
    expect(getStored('k', 'local')).toBe('1');
    vi.unstubAllGlobals();
    expect(localStorage.getItem('k')).toBeNull(); // the value lives in memory only
    blockStorage();
    removeStored('k', 'local');
    expect(getStored('k', 'local')).toBeNull();
  });
});

describe('useStored', () => {
  it('renders the server snapshot as null even when a value is stored', () => {
    setStored('k', 'a', 'local');
    expect(renderToString(<Probe />)).toContain('none');
  });

  it('reads the stored value and follows same-tab writes and removes', () => {
    setStored('k', 'a', 'local');
    const view = mount();
    expect(view.text()).toBe('a');
    act(() => setStored('k', 'b', 'local'));
    expect(view.text()).toBe('b');
    act(() => removeStored('k', 'local'));
    expect(view.text()).toBe('none');
  });

  it('updates a mounted hook from a write that storage refused', () => {
    blockStorage();
    const view = mount();
    act(() => setStored('k', 'x', 'local'));
    expect(view.text()).toBe('x');
  });
});
