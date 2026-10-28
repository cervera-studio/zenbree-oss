import type { Preview } from '@/lib/campaigns/api';
import type { CampaignField } from '@/lib/campaigns/fields';
import { blankStep, type Channel, hasCopyB, type Step } from '@/lib/campaigns/steps';

type BuilderState = {
  steps: Step[];
  fields: CampaignField[];
  dirty: boolean;
  // The A/B switch. On with no copy B yet is a real state: the user is about to write it, and activation waits.
  abTest: boolean;
  open: number | null;
  notice: string | null;
  preview: { items: Preview['items']; status: 'ready' | 'loading' | 'failed' };
};

export type Action =
  | { type: 'stepChanged'; index: number; step: Step }
  | { type: 'stepAdded'; channel: Channel }
  | { type: 'stepRemoved'; index: number }
  | { type: 'stepRestored'; index: number; step: Step }
  | { type: 'testSwitched'; on: boolean }
  | { type: 'fieldsEdited'; fields: CampaignField[] }
  | { type: 'stepToggled'; index: number }
  | { type: 'previewLoaded'; items: Preview['items'] }
  | { type: 'previewFailed' }
  | { type: 'requestStarted' }
  | { type: 'noticeShown'; text: string }
  | { type: 'stepsSaved' }
  | { type: 'campaignLoaded'; steps: Step[]; fields: CampaignField[]; notice?: string };

export function initialState({
  steps,
  fields,
  items,
}: {
  steps: Step[];
  fields: CampaignField[];
  items: Preview['items'];
}): BuilderState {
  return {
    steps,
    fields,
    dirty: false,
    abTest: hasCopyB(steps),
    open: steps.length ? 0 : null,
    notice: null,
    preview: { items, status: 'ready' },
  };
}

export function reducer(state: BuilderState, action: Action): BuilderState {
  switch (action.type) {
    case 'stepChanged':
      return edited(
        state,
        state.steps.map((s, i) => (i === action.index ? action.step : s)),
        state.open,
      );
    case 'stepAdded':
      return edited(
        state,
        [...state.steps, blankStep(state.steps.at(-1), action.channel)],
        state.steps.length,
      );
    case 'stepRemoved':
      return edited(
        state,
        state.steps.filter((_, i) => i !== action.index),
        null,
      );
    case 'stepRestored':
      // A step removed before the test went off must not bring its copy B back with it.
      return edited(
        state,
        state.steps.toSpliced(action.index, 0, state.abTest ? action.step : withoutB(action.step)),
        // The open step moves down with the others, so that its card is not handed to the one put back.
        state.open !== null && state.open >= action.index ? state.open + 1 : state.open,
      );
    case 'testSwitched':
      // Off ends the test: every copy B goes, as the API stores no test without one.
      return action.on
        ? { ...state, abTest: true }
        : { ...edited(state, state.steps.map(withoutB), state.open), abTest: false };
    case 'fieldsEdited':
      return { ...edited(state, state.steps, state.open), fields: action.fields };
    case 'stepToggled':
      return { ...state, open: state.open === action.index ? null : action.index };
    case 'previewLoaded':
      return { ...state, preview: { items: action.items, status: 'ready' } };
    case 'previewFailed':
      return { ...state, preview: { ...state.preview, status: 'failed' } };
    case 'requestStarted':
      return { ...state, notice: null };
    case 'noticeShown':
      return { ...state, notice: action.text };
    case 'stepsSaved':
      return { ...state, dirty: false };
    case 'campaignLoaded':
      return {
        ...state,
        steps: action.steps,
        fields: action.fields,
        dirty: false,
        abTest: state.abTest || hasCopyB(action.steps),
        notice: action.notice ?? state.notice,
      };
  }
}

function withoutB({ b: _, ...step }: Step): Step {
  return step;
}

function edited(state: BuilderState, steps: Step[], open: number | null): BuilderState {
  return { ...state, steps, dirty: true, open, preview: { ...state.preview, status: 'loading' } };
}
