import { useSyncExternalStore } from 'react';
import { getStored, type StorageArea, subscribeStored } from './storage';

/** The stored string, or null when absent or unreadable. `server` is what the server render and
 *  hydration see, null unless a caller must hide something until the client has read storage. */
export function useStored(key: string, area: StorageArea, server: string | null = null) {
  return useSyncExternalStore(
    subscribeStored,
    () => getStored(key, area),
    () => server,
  );
}
