export type StorageArea = 'local' | 'session';

const memory = new Map<string, string>();
const listeners = new Set<() => void>();

const slot = (key: string, area: StorageArea) => `${area}:${key}`;

function backend(area: StorageArea) {
  return area === 'local' ? localStorage : sessionStorage;
}

function notify() {
  for (const listener of listeners) listener();
}

/** Never throws: private windows and blocked site data make every storage access throw. */
export function getStored(key: string, area: StorageArea): string | null {
  const remembered = memory.get(slot(key, area));
  if (remembered !== undefined) return remembered;
  try {
    return backend(area).getItem(key);
  } catch {
    return null;
  }
}

/** A write that storage refuses stays readable until the page unloads, so the page behaves the same
 *  for the rest of its life. */
export function setStored(key: string, value: string, area: StorageArea) {
  try {
    backend(area).setItem(key, value);
    memory.delete(slot(key, area));
  } catch {
    memory.set(slot(key, area), value);
  }
  notify();
}

export function removeStored(key: string, area: StorageArea) {
  memory.delete(slot(key, area));
  try {
    backend(area).removeItem(key);
  } catch {}
  notify();
}

export function subscribeStored(onChange: () => void) {
  listeners.add(onChange);
  return () => {
    listeners.delete(onChange);
  };
}
