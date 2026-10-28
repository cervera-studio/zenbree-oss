import { describe, expect, it } from 'vite-plus/test';
import type { Job } from '@/lib/lists/api';
import { failedStage, type JobStage, liveJob, parseProgress, stepState } from './job-progress';

const stage = (over: Partial<JobStage> = {}): JobStage => ({
  tool: 'find_emails',
  label: 'Ricerca email',
  units: 50,
  done: 14,
  ...over,
});

describe('parseProgress', () => {
  it('reads a stages frame and keeps a failed stage with its code', () => {
    const progress = parseProgress({
      stages: [
        { tool: 'a', label: 'A', units: 10, done: 10 },
        { tool: 'b', label: 'B', units: 100, done: 64, error: { code: 'provider' } },
      ],
      phase: null,
      creditsSpent: 86,
    });
    expect(progress?.creditsSpent).toBe(86);
    expect(progress && failedStage(progress)).toMatchObject({ index: 1, stage: { done: 64 } });
  });

  it('turns an error code it does not know into a plain failure', () => {
    const progress = parseProgress({
      stages: [{ tool: 'b', label: 'B', units: 1, done: 0, error: { code: 'new_code' } }],
    });
    expect(progress?.stages[0]?.error).toEqual({ code: 'failed' });
  });

  it('is null when the job runs no plan or the payload is not stages', () => {
    expect(parseProgress(null)).toBeNull();
    expect(parseProgress({ id: 'j', status: 'succeeded', stages: null })).toBeNull();
    expect(parseProgress({ stages: [] })).toBeNull();
    expect(parseProgress({ stages: [{ tool: 'a' }] })).toBeNull();
  });
});

describe('stepState', () => {
  it.each([
    ['a failed call', stage({ error: { code: 'provider' } }), null, true, 'failed'],
    ['all units in', stage({ done: 50 }), null, true, 'done'],
    ['units flowing', stage(), null, true, 'running'],
    [
      'its tool in flight with nothing done yet',
      stage({ done: 0 }),
      'find_emails',
      true,
      'running',
    ],
    ['nothing yet and another tool running', stage({ done: 0 }), 'other', true, 'queued'],
    ['a run that ended short', stage(), null, false, 'stopped'],
    ['a run that ended before it started', stage({ done: 0 }), null, false, 'queued'],
  ] as const)('%s', (_, s, phase, running, expected) => {
    expect(stepState(s, phase, running)).toBe(expected);
  });
});

describe('liveJob', () => {
  const snapshot: Job = {
    id: 'j1',
    chat_session_id: null,
    status: 'queued',
    phase: null,
    progress: { done: 0, total: 0 },
    list_id: null,
    credits: { reserved: 100, spent: 0 },
    stages: null,
    error: null,
    created_at: '2026-10-05T10:00:00Z',
    finished_at: null,
  };

  it('shows a streaming turn as running before the worker reports, but keeps a terminal snapshot', () => {
    expect(liveJob(snapshot, { progress: null, streaming: true }).status).toBe('running');
    const done: Job = { ...snapshot, status: 'succeeded', credits: { reserved: 100, spent: 7 } };
    expect(liveJob(done, { progress: null, streaming: true })).toEqual(done);
  });

  it('takes stages, phase and spend from the latest stages frame', () => {
    const job = liveJob(snapshot, {
      streaming: true,
      progress: {
        stages: [stage({ units: 10, done: 10 }), stage({ tool: 'b', units: 50, done: 60 })],
        phase: 'b',
        creditsSpent: 42,
        retryStage: null,
        listId: null,
      },
    });
    expect(job).toMatchObject({
      status: 'running',
      phase: 'b',
      progress: { done: 60, total: 60 },
      credits: { reserved: 100, spent: 42 },
    });
    expect(job.stages).toHaveLength(2);
  });
});
