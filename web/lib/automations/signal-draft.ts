import type { Automation } from './api';

// A draft keeps these while another trigger is picked, as a step keeps its fields while it is off. `watchlistId` is
// the picker's value: `''` when none is picked or the saved one was deleted.
export type SignalFields = { watchlistId: string; minScore: number };

// A job posting scores 100 when it goes up: by default one fresh posting is enough.
const DEFAULT_MIN_SCORE = 100;
export const MAX_SIGNAL_SCORE = 10000;

export function signalFieldsOf(saved: Pick<Automation, 'signal'> | null): SignalFields {
  return {
    watchlistId: saved?.signal?.watchlistId ?? '',
    minScore: saved?.signal?.minScore ?? DEFAULT_MIN_SCORE,
  };
}

/** As `SignalTrigger` validates: a sum of decaying scores, so any number above 0 counts. */
export const signalScoreOk = (n: number) => Number.isFinite(n) && n > 0 && n <= MAX_SIGNAL_SCORE;
