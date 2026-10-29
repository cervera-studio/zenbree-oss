import type { Tone } from '@/components/ui/badge';
import type { Job } from '@/lib/admin/api';

/**
 * Each API status: its tone (chip dot, badge, stage dot), the tone of its progress meter and its
 * key under `admin.jobs.statuses`, which is also its key in `counts` (camelCased on the way in).
 * In the order of the chips.
 */
export const JOB_STATUS = {
  running: { tone: 'accent', meter: 'accent', label: 'running' },
  queued: { tone: 'neutral', meter: 'accent', label: 'queued' },
  needs_input: { tone: 'warning', meter: 'warning', label: 'needsInput' },
  succeeded: { tone: 'positive', meter: 'accent', label: 'succeeded' },
  failed: { tone: 'negative', meter: 'accent', label: 'failed' },
  cancelled: { tone: 'neutral', meter: 'accent', label: 'cancelled' },
} as const satisfies Record<Job['status'], { tone: Tone; meter: Tone; label: string }>;

export type JobStatus = keyof typeof JOB_STATUS;

export const JOB_STATUSES = Object.keys(JOB_STATUS) as JobStatus[];

export const isJobStatus = (s: unknown): s is JobStatus =>
  typeof s === 'string' && Object.hasOwn(JOB_STATUS, s);

type Stage = NonNullable<Job['stages']>[number];

/** A stage that ended: it failed, or it did all its units. */
const stageFinished = (s: Stage) => !!s.error || s.done >= s.units;

/** A stage's dot: failed, done, in the state of its job while that waits or runs, else amber. */
export const stageTone = (s: Stage, job: Job['status']): Tone => {
  if (s.error) return 'negative';
  if (stageFinished(s)) return 'positive';
  return job === 'running' || job === 'needs_input' ? JOB_STATUS[job].tone : 'warning';
};

/** What a stage line says: `done/units` until the stage did all its units, and its time once it
 *  started (running or finished). A stage that completed shows its time only. */
export const stageText = (
  s: Stage,
  count: (v: number) => string,
  duration: (startedAt: string) => string,
) => {
  const parts: string[] = [];
  if (s.error || !stageFinished(s) || !s.startedAt)
    parts.push(`${count(s.done)}/${count(s.units)}`);
  if (s.startedAt) parts.push(duration(s.startedAt));
  return parts.join(' · ');
};

// Header keys in order, with whether the column is numeric; the details toggle is one more.
export const COLUMNS = [
  ['id', false],
  ['user', false],
  ['status', false],
  ['phase', false],
  ['progress', false],
  ['credits', true],
  ['created', false],
  ['timeToFirstRow', true],
  ['totalDuration', true],
] as const;
