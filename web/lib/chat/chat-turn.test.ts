import fc from 'fast-check';
import { expect, it } from 'vite-plus/test';
import { type ChatTurn, IDLE, jobNamedAfterStop, nextTurn } from './chat-turn';

it('ignores a stop failure that comes after its turn', () => {
  expect(nextTurn(IDLE, { type: 'stopFailed', jobId: 'j1' })).toBe(IDLE);
  const next: ChatTurn = { phase: 'stopping', jobId: 'j2' };
  expect(nextTurn(next, { type: 'stopFailed', jobId: 'j1' })).toBe(next);
});

it('keeps the current job through any number of failed rejoins', () => {
  fc.assert(
    fc.property(
      fc.option(fc.uuid(), { nil: null }),
      fc.uuid(),
      fc.nat({ max: 20 }),
      (jobId, staleJobId, retries) => {
        let turn: ChatTurn = { phase: 'streaming', jobId };
        for (let i = 0; i <= retries; i++) {
          turn = nextTurn(turn, { type: 'dropped' });
          turn = nextTurn(turn, { type: 'rejoined', jobId: staleJobId });
        }
        expect(nextTurn(turn, { type: 'stopPressed' })).toEqual(
          jobId ? { phase: 'stopping', jobId } : IDLE,
        );
        expect(nextTurn(turn, { type: 'attached' })).toEqual({ phase: 'streaming', jobId });
      },
    ),
  );
});

const job = '00000000-0000-1000-8000-000000000000';
it.each([
  [job, job],
  [job, 'previous-job'],
  [null, 'previous-job'],
])('stops only a known current job after a drop (%s)', (jobId, previousJobId) => {
  const lost = nextTurn({ phase: 'streaming', jobId }, { type: 'dropped' });
  const rejoining = nextTurn(lost, { type: 'rejoined', jobId: previousJobId });
  expect(nextTurn(rejoining, { type: 'stopPressed' })).toEqual(
    jobId ? { phase: 'stopping', jobId } : IDLE,
  );
});

it('carries a pending Stop through repeated drops without adopting a stale job', () => {
  fc.assert(
    fc.property(fc.uuid(), fc.uuid(), fc.nat({ max: 20 }), (jobId, staleJobId, retries) => {
      let turn = nextTurn(nextTurn(IDLE, { type: 'sent' }), { type: 'stopPressed' });
      for (let i = 0; i <= retries; i++) {
        turn = nextTurn(turn, { type: 'dropped' });
        turn = nextTurn(turn, { type: 'rejoined', jobId: staleJobId });
        expect(turn.phase !== 'idle' && turn.jobId).toBeNull();
      }
      turn = nextTurn(turn, { type: 'attached' });
      const named = nextTurn(turn, { type: 'jobStarted', jobId });
      expect(jobNamedAfterStop(turn, named)).toBe(jobId);
      expect(jobNamedAfterStop(named, nextTurn(named, { type: 'jobStarted', jobId }))).toBeNull();
    }),
  );
});

it('keeps the minimal pending Stop counterexample', () => {
  let turn: ChatTurn = IDLE;
  for (const event of [
    { type: 'sent' },
    { type: 'stopPressed' },
    { type: 'dropped' },
    { type: 'rejoined', jobId: job },
    { type: 'attached' },
  ] as const)
    turn = nextTurn(turn, event);
  expect(jobNamedAfterStop(turn, nextTurn(turn, { type: 'jobStarted', jobId: job }))).toBe(job);
});
