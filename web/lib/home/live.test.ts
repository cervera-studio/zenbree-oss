import { describe, expect, it } from 'vite-plus/test';
import {
  DONE_ROW_MAX_MS,
  type HomeEvent,
  type HomeLive,
  homeReducer,
  initHome,
  phaseEvent,
  visibleLists,
} from './live';
import type { HomeOverview } from './overview';
import { finishedOf, listOf, overviewOf, runOf } from './test-overview';

const snap = (overview: HomeOverview, silent = false, at = 0): HomeEvent => ({
  type: 'snapshot',
  overview,
  silent,
  at,
});

const play = (state: HomeLive, ...events: HomeEvent[]) => events.reduce(homeReducer, state);

const withApprovals = (total: number) => overviewOf({ approvals: { total, campaigns: [] } });

const reply = (count: number, name = 'Marco Bellini') =>
  overviewOf({
    replies: {
      count,
      latest: { name, campaign: 'Fiere 2026', intent: 'interested', at: '2026-10-07T09:00:00Z' },
    },
    campaigns: [
      {
        id: 'c1',
        name: 'Fiere 2026',
        status: 'active',
        channels: ['email'],
        sent: 80,
        replies: count,
        owner: null,
      },
    ],
  });

describe('snapshot', () => {
  it('pops and announces nothing when the frame is the first of a connection', () => {
    const state = play(initHome(withApprovals(2)), snap(withApprovals(9), true));
    expect(state.overview.approvals.total).toBe(9);
    expect(state.pulses).toEqual({});
    expect(state.announced).toEqual([]);
  });

  it('pops and announces when approvals go up', () => {
    const state = play(initHome(withApprovals(14)), snap(withApprovals(16)));
    expect(state.pulses.approvals).toBe(1);
    expect(state.announced).toMatchObject([{ kind: 'approvals', added: 2, total: 16 }]);
  });

  it('only updates when approvals go down', () => {
    const state = play(initHome(withApprovals(16)), snap(withApprovals(3)));
    expect(state.overview.approvals.total).toBe(3);
    expect(state.pulses).toEqual({});
    expect(state.announced).toEqual([]);
  });

  it('pops the replies card and the campaign, and names the sender and the intent', () => {
    const state = play(initHome(reply(3)), snap(reply(4)));
    expect(state.pulses).toEqual({ replies: 1, 'campaign:c1': 1 });
    expect(state.announced).toMatchObject([
      { kind: 'reply', name: 'Marco Bellini', intent: 'interested', count: 4 },
    ]);
  });

  it('returns the same state for an equal snapshot', () => {
    const state = initHome(reply(3));
    expect(homeReducer(state, snap(reply(3)))).toBe(state);
  });

  it('keeps the id of an announced item when a later one joins', () => {
    const a = play(initHome(reply(3)), snap(reply(4)));
    const first = a.announced[0]?.id ?? -1;
    const b = play(a, snap(reply(5)));
    expect(b.announced.map((x) => x.id)).toEqual([first, first + 1]);
  });
});

describe('a run that finishes', () => {
  const running = overviewOf({ running: [runOf()], lists: [listOf('l1')] });
  const finished = overviewOf({ finished: [finishedOf()], lists: [listOf('l1')] });

  it('turns done, then leaves, and only then shows its list as new', () => {
    let state = initHome(running);
    expect(visibleLists(state)).toEqual([]);

    state = play(state, snap(finished));
    expect(state.runs).toMatchObject([{ phase: 'done', jobId: 'j1' }]);
    expect(visibleLists(state)).toEqual([]);
    expect(state.announced).toEqual([]);

    state = play(state, { type: 'held', jobId: 'j1' });
    expect(state.runs).toMatchObject([{ phase: 'leaving' }]);
    expect(visibleLists(state)).toEqual([]);

    state = play(state, { type: 'left', jobId: 'j1' });
    expect(state.runs).toEqual([]);
    expect(visibleLists(state).map((l) => l.id)).toEqual(['l1']);
    expect(state.fresh).toEqual(['l1']);
    expect(state.announced).toMatchObject([
      { kind: 'finished', title: 'Agenzie Bologna', entity: 'person', rows: 41 },
    ]);
  });

  it('keeps a reply that lands during the hold when the finish follows', () => {
    const replied = (count: number) => ({ ...reply(count), finished: [finishedOf()] });
    let state = initHome({ ...reply(3), running: [runOf()] });
    state = play(state, snap(replied(3)), snap(replied(4)), { type: 'held', jobId: 'j1' });
    state = play(state, { type: 'left', jobId: 'j1' });
    expect(state.announced.map((a) => a.kind)).toEqual(['reply', 'finished']);
  });

  it('keeps only the newest few announcements', () => {
    let state = initHome(withApprovals(0));
    for (let total = 1; total <= 10; total++) state = play(state, snap(withApprovals(total)));
    expect(state.announced.map((a) => (a.kind === 'approvals' ? a.total : 0))).toEqual([8, 9, 10]);
  });

  it('does not badge a list the run only extended', () => {
    const extending = overviewOf({ running: [runOf({ newList: false })], lists: [listOf('l1')] });
    const done = overviewOf({
      finished: [finishedOf({ newList: false })],
      lists: [listOf('l1')],
    });
    expect(visibleLists(initHome(extending)).map((l) => l.id)).toEqual(['l1']);
    const state = play(initHome(extending), snap(done), { type: 'left', jobId: 'j1' });
    expect(state.fresh).toEqual([]);
  });

  it('leaves silently when the run is in neither running nor finished', () => {
    const state = play(initHome(running), snap(overviewOf({ lists: [listOf('l1')] })));
    expect(state.runs).toEqual([]);
    expect(state.announced).toEqual([]);
    expect(state.fresh).toEqual([]);
  });

  it('ignores a finished entry it never saw running', () => {
    const state = play(initHome(overviewOf()), snap(finished));
    expect(state.runs).toEqual([]);
  });

  it('does not play a finish it learns of on a first frame', () => {
    const state = play(initHome(running), snap(finished, true));
    expect(state.runs).toEqual([]);
    expect(state.announced).toEqual([]);
  });

  it('keeps a finish that is playing when a first frame arrives', () => {
    const state = play(initHome(running), snap(finished), snap(finished, true));
    expect(state.runs).toMatchObject([{ phase: 'done' }]);
  });

  it('ignores a repeated left and a held for a running run', () => {
    const done = play(initHome(running), snap(finished), { type: 'left', jobId: 'j1' });
    expect(homeReducer(done, { type: 'left', jobId: 'j1' })).toBe(done);
    const live = initHome(running);
    expect(homeReducer(live, { type: 'held', jobId: 'j1' })).toBe(live);
    expect(homeReducer(live, { type: 'left', jobId: 'j1' })).toBe(live);
  });

  it('resolves a done row whose animation events never came on the next snapshot', () => {
    const done = play(initHome(running), snap(finished, false, 1_000));
    const early = homeReducer(done, snap(finished, false, 1_000 + DONE_ROW_MAX_MS));
    expect(early).toBe(done);
    const late = homeReducer(done, snap(finished, false, 1_001 + DONE_ROW_MAX_MS));
    expect(late.runs).toEqual([]);
    expect(late.fresh).toEqual(['l1']);
    expect(late.announced[0]).toMatchObject({ kind: 'finished' });
  });

  it('keeps the position of a row when its neighbour changes', () => {
    const two = overviewOf({
      running: [runOf(), runOf({ jobId: 'j2', listId: 'l2', sessionId: 's2' })],
    });
    const state = play(
      initHome(two),
      snap(
        overviewOf({
          running: [runOf({ jobId: 'j2', listId: 'l2', sessionId: 's2' })],
          finished: [finishedOf()],
        }),
      ),
    );
    expect(state.runs.map((r) => [r.jobId, r.phase])).toEqual([
      ['j1', 'done'],
      ['j2', 'running'],
    ]);
  });
});

describe('phaseEvent', () => {
  it('maps the leave animation on the row to held at its start and left at its end', () => {
    expect(phaseEvent('j1', 'start', 'home-leave', true)).toEqual({ type: 'held', jobId: 'j1' });
    expect(phaseEvent('j1', 'end', 'home-leave', true)).toEqual({ type: 'left', jobId: 'j1' });
  });

  it('ignores an animation of a child or another name', () => {
    expect(phaseEvent('j1', 'end', 'home-leave', false)).toBeNull();
    expect(phaseEvent('j1', 'end', 'spin', true)).toBeNull();
  });
});
