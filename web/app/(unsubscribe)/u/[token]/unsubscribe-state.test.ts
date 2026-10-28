import { describe, expect, it } from 'vite-plus/test';
import { type Action, initialState, reducer, type UnsubscribeState } from './unsubscribe-state';

const run = (state: UnsubscribeState, ...actions: Action[]) => actions.reduce(reducer, state);
const ask = initialState('subscribed', null);
const done = run(ask, { type: 'unsubscribed', already: false });

describe('unsubscribe reducer', () => {
  it('starts in ask, or in already with the date from the page load', () => {
    expect(ask).toEqual({ view: 'ask', reason: null, failed: false });
    expect(initialState('unsubscribed', '2026-10-01')).toEqual({
      view: 'already',
      since: '2026-10-01',
      reason: null,
      failed: false,
    });
  });

  it('a fresh unsubscribe goes to done with undo and an idle erasure', () => {
    expect(done).toEqual({
      view: 'done',
      canUndo: true,
      erasure: 'idle',
      reason: null,
      failed: false,
    });
  });

  it('a click that finds the person already out goes to already with no date', () => {
    expect(run(ask, { type: 'unsubscribed', already: true })).toEqual({
      view: 'already',
      since: null,
      reason: null,
      failed: false,
    });
  });

  it('undo returns to ask and keeps the reason', () => {
    const state = run(
      ask,
      { type: 'reasonChosen', reason: 'too_many' },
      { type: 'unsubscribed', already: false },
      { type: 'erased', sent: true },
      { type: 'undone' },
    );
    expect(state).toEqual({ view: 'ask', reason: 'too_many', failed: false });
  });

  it('a failed undo hides the undo link and stays in done', () => {
    expect(run(done, { type: 'undoRefused' })).toMatchObject({ view: 'done', canUndo: false });
  });

  it('erasure becomes sent on success or 429, failed otherwise', () => {
    expect(run(done, { type: 'erased', sent: true })).toMatchObject({ erasure: 'sent' });
    expect(run(done, { type: 'erased', sent: false })).toMatchObject({ erasure: 'failed' });
  });

  it('settled sets the failed flag from the outcome', () => {
    const failed = run(ask, { type: 'settled', ok: false });
    expect(failed.failed).toBe(true);
    expect(reducer(failed, { type: 'settled', ok: true }).failed).toBe(false);
  });

  it('an action outside its view returns the same state', () => {
    const already = run(ask, { type: 'unsubscribed', already: true });
    expect(reducer(done, { type: 'unsubscribed', already: false })).toBe(done);
    expect(reducer(done, { type: 'reasonChosen', reason: 'too_many' })).toBe(done);
    expect(reducer(ask, { type: 'undone' })).toBe(ask);
    expect(reducer(ask, { type: 'undoRefused' })).toBe(ask);
    expect(reducer(ask, { type: 'erased', sent: true })).toBe(ask);
    expect(reducer(already, { type: 'undone' })).toBe(already);
    expect(reducer(already, { type: 'erased', sent: true })).toBe(already);
  });
});
