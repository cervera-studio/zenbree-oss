import type { Reason } from './reasons';

type Erasure = 'idle' | 'sent' | 'failed';
type Common = { reason: Reason | null; failed: boolean };

/** Each view carries only its own data: `since` exists only in already, undo and erasure only in done. */
export type UnsubscribeState =
  | ({ view: 'ask' } & Common)
  | ({ view: 'already'; since: string | null } & Common)
  | ({ view: 'done'; canUndo: boolean; erasure: Erasure } & Common);

export type Action =
  | { type: 'reasonChosen'; reason: Reason }
  | { type: 'unsubscribed'; already: boolean }
  | { type: 'undone' }
  | { type: 'undoRefused' }
  | { type: 'erased'; sent: boolean }
  | { type: 'settled'; ok: boolean };

export function initialState(
  status: 'subscribed' | 'unsubscribed',
  unsubscribedOn: string | null,
): UnsubscribeState {
  const common = { reason: null, failed: false };
  return status === 'unsubscribed'
    ? { view: 'already', since: unsubscribedOn, ...common }
    : { view: 'ask', ...common };
}

export function reducer(state: UnsubscribeState, action: Action): UnsubscribeState {
  switch (action.type) {
    case 'reasonChosen':
      return state.view === 'ask' ? { ...state, reason: action.reason } : state;
    case 'unsubscribed': {
      if (state.view !== 'ask') return state;
      const { reason, failed } = state;
      // A click that finds the person already out has no date to show; the page load does.
      // Undo lifts only what this click added: an earlier exclusion is the sender's, not ours to lift.
      return action.already
        ? { view: 'already', since: null, reason, failed }
        : { view: 'done', canUndo: true, erasure: 'idle', reason, failed };
    }
    case 'undone':
      return state.view === 'done'
        ? { view: 'ask', reason: state.reason, failed: state.failed }
        : state;
    case 'undoRefused':
      return state.view === 'done' ? { ...state, canUndo: false } : state;
    case 'erased':
      return state.view === 'done' ? { ...state, erasure: action.sent ? 'sent' : 'failed' } : state;
    case 'settled':
      return { ...state, failed: !action.ok };
  }
}
