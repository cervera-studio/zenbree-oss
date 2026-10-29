import { type Job, TERMINAL_JOB } from '@/lib/lists/api';

const STAGE_ERROR_CODES = [
  'provider',
  'failed',
  'disabled',
  'no_credits',
  'over_budget',
  'crashed',
  'not_connected',
] as const;
type StageErrorCode = (typeof STAGE_ERROR_CODES)[number];

// a type, not an interface: it is assigned to the zod job shape's index-signature stages
export type JobStage = {
  tool: string;
  label: string;
  units: number;
  done: number;
  /** `units` count rows; else calls, which for a search tool are searches. */
  perRow?: boolean;
  error?: { code: StageErrorCode };
};

export interface JobProgress {
  stages: JobStage[];
  phase: string | null;
  creditsSpent: number;
  retryStage: number | null;
  listId: string | null;
}

export type StepState = 'done' | 'running' | 'queued' | 'failed' | 'stopped';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function errorCode(raw: unknown): StageErrorCode {
  const code = isRecord(raw) ? raw.code : null;
  return STAGE_ERROR_CODES.find((c) => c === code) ?? 'failed';
}

function parseStage(raw: unknown): JobStage | null {
  if (!isRecord(raw)) return null;
  const { tool, label, units, done, error, perRow } = raw;
  if (typeof tool !== 'string' || typeof units !== 'number' || typeof done !== 'number')
    return null;
  return {
    tool,
    label: typeof label === 'string' && label ? label : tool,
    units,
    done,
    ...(perRow === true ? { perRow } : {}),
    ...(error ? { error: { code: errorCode(error) } } : {}),
  };
}

export function parseProgress(raw: unknown): JobProgress | null {
  if (!isRecord(raw) || !Array.isArray(raw.stages)) return null;
  const stages = raw.stages.flatMap((s) => parseStage(s) ?? []);
  if (stages.length === 0) return null;
  return {
    stages,
    phase: typeof raw.phase === 'string' ? raw.phase : null,
    creditsSpent: typeof raw.creditsSpent === 'number' ? raw.creditsSpent : 0,
    retryStage: typeof raw.retryStage === 'number' ? raw.retryStage : null,
    listId: typeof raw.listId === 'string' ? raw.listId : null,
  };
}

export function stepState(stage: JobStage, phase: string | null, running: boolean): StepState {
  if (stage.error) return 'failed';
  if (stage.done >= stage.units) return 'done';
  if (!running) return stage.done > 0 ? 'stopped' : 'queued';
  return stage.done > 0 || phase === stage.tool ? 'running' : 'queued';
}

export function failedStage(progress: JobProgress): { index: number; stage: JobStage } | null {
  const index = progress.stages.findIndex((s) => s.error);
  const stage = progress.stages[index];
  return stage ? { index, stage } : null;
}

/** Retrying can fix these; `no_credits`, `over_budget` and `disabled` need the user or an admin. */
export const RETRYABLE_CODES: readonly string[] = ['provider', 'failed', 'crashed'];

/** Nothing was delivered and every error is one a retry can fix: the run failed as a whole. */
export function allStagesFailed(progress: JobProgress): boolean {
  return (
    progress.stages.some((s) => s.error) &&
    progress.stages.every(
      (s) => s.done === 0 && (!s.error || RETRYABLE_CODES.includes(s.error.code)),
    )
  );
}

export function overallProgress(progress: JobProgress): { done: number; total: number } {
  return progress.stages.reduce(
    (sum, s) => ({ done: sum.done + Math.min(s.done, s.units), total: sum.total + s.units }),
    { done: 0, total: 0 },
  );
}

/** What the chat store knows live about its turn's job (#265): the `stages` frames and the stream. */
export interface LiveJob {
  id: string;
  progress: JobProgress | null;
  streaming: boolean;
}

/** A `GET /v1/jobs/{id}` snapshot brought up to date with the turn's SSE stream (#265). */
export function liveJob(job: Job, live: Omit<LiveJob, 'id'>): Job {
  // the snapshot can be read before the worker picks the job up; while the turn streams it runs
  const status = live.streaming && !TERMINAL_JOB.has(job.status) ? 'running' : job.status;
  const { progress } = live;
  if (!progress) return { ...job, status };
  return {
    ...job,
    status,
    phase: progress.phase,
    progress: overallProgress(progress),
    stages: progress.stages,
    credits: { ...job.credits, spent: progress.creditsSpent },
  };
}
