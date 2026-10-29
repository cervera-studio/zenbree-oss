export const RECONNECT_TRIES = 10;

const BASE_MS = 1000;
const MAX_MS = 15_000;

/** Wait before try number `tries` (0-based): 1 s, 2 s, 4 s, … up to 15 s. */
export function reconnectDelay(tries: number): number {
  return Math.min(BASE_MS * 2 ** tries, MAX_MS);
}
