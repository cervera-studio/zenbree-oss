import { z } from 'zod';
import { api } from '@/lib/api/browser';
import type { Schema } from '@/lib/api/schema';
import type { CreditPrices, ListColumn } from '@/lib/lists/api';
import type { FillEvent } from '@/lib/lists/fill-stream';
import type { AppErrorJSON } from '@/lib/types';

export const MAX_INPUTS = 10;
export const MAX_LABEL = 60;
export const MAX_PROMPT = 500;
const MIN_PROMPT = 3;

const snippetRowsSchema = z.looseObject({
  list_id: z.string().nullable(),
  row_ids: z.array(z.string()),
  total: z.number(),
}) satisfies z.ZodType<Schema<'SnippetRows'>>;

export const snippetRows = (campaignId: string) =>
  api(`/v1/campaigns/${encodeURIComponent(campaignId)}/snippet-rows`, {
    camel: false,
    schema: snippetRowsSchema,
  });

export type ResearchColumn = { key: string; label: string };

export const researchColumns = (columns: ListColumn[]): ResearchColumn[] =>
  columns
    .filter((c) => c.kind === 'ai' && c.config?.mode !== 'snippet')
    .map((c) => ({ key: c.key, label: c.label }));

type Setup = {
  listId: string;
  rowIds: string[];
  onList: number;
  research: ResearchColumn[];
  prices: CreditPrices;
};

export type Estimate =
  | { status: 'idle' }
  | { status: 'loading'; inputs: string }
  | { status: 'ready'; inputs: string; researchRows: number; credits: number }
  | { status: 'failed'; inputs: string; error: AppErrorJSON };

export type Run =
  | { status: 'idle'; error: AppErrorJSON | null }
  | { status: 'creating' }
  | { status: 'filling'; key: string; total: number; written: string[] }
  | { status: 'done'; key: string; filled: number; credits: number }
  | { status: 'failed'; key: string; fillError: string | null; streamCode: string | null };

export type SnippetState =
  | { phase: 'loading' }
  | { phase: 'unavailable'; error: AppErrorJSON | null }
  | { phase: 'ready'; setup: Setup; estimate: Estimate; run: Run };

export type SnippetAction =
  | { type: 'loaded'; setup: Setup }
  | { type: 'loadFailed'; error: AppErrorJSON | null }
  | { type: 'estimating'; inputs: string }
  | { type: 'estimateCleared' }
  | { type: 'estimated'; inputs: string; researchRows: number; credits: number }
  | { type: 'estimateFailed'; inputs: string; error: AppErrorJSON }
  | { type: 'creating' }
  | { type: 'createFailed'; error: AppErrorJSON }
  | { type: 'created'; key: string; total: number }
  | { type: 'frame'; event: FillEvent }
  | { type: 'streamFailed'; code: string };

export const inputsKey = (inputs: string[]) => [...inputs].sort().join('\n');

export const initialSnippetState: SnippetState = { phase: 'loading' };

export function snippetReducer(state: SnippetState, action: SnippetAction): SnippetState {
  if (action.type === 'loaded') {
    return {
      phase: 'ready',
      setup: action.setup,
      estimate: { status: 'idle' },
      run: { status: 'idle', error: null },
    };
  }
  if (action.type === 'loadFailed') return { phase: 'unavailable', error: action.error };
  if (state.phase !== 'ready') return state;
  return { ...state, ...step(state, action) };
}

const awaits = (estimate: Estimate, inputs: string) =>
  estimate.status === 'loading' && estimate.inputs === inputs;

function step(
  { estimate, run }: Extract<SnippetState, { phase: 'ready' }>,
  action: SnippetAction,
): Partial<Extract<SnippetState, { phase: 'ready' }>> {
  switch (action.type) {
    case 'estimating':
      return { estimate: { status: 'loading', inputs: action.inputs } };
    case 'estimateCleared':
      return { estimate: { status: 'idle' } };
    case 'estimated':
      if (!awaits(estimate, action.inputs)) return {};
      return {
        estimate: {
          status: 'ready',
          inputs: action.inputs,
          researchRows: action.researchRows,
          credits: action.credits,
        },
      };
    case 'estimateFailed':
      if (!awaits(estimate, action.inputs)) return {};
      return { estimate: { status: 'failed', inputs: action.inputs, error: action.error } };
    case 'creating':
      return { run: { status: 'creating' } };
    case 'createFailed':
      return { run: { status: 'idle', error: action.error } };
    case 'created':
      return { run: { status: 'filling', key: action.key, total: action.total, written: [] } };
    case 'frame':
      return run.status === 'filling' ? { run: onFrame(run, action.event) } : {};
    case 'streamFailed':
      if (run.status !== 'filling') return {};
      return { run: { status: 'failed', key: run.key, fillError: null, streamCode: action.code } };
    default:
      return {};
  }
}

function onFrame(run: Extract<Run, { status: 'filling' }>, ev: FillEvent): Run {
  if (ev.type === 'row') {
    if (!ev.cell_meta[run.key] || run.written.includes(ev.row_id)) return run;
    return { ...run, written: [...run.written, ev.row_id] };
  }
  if (ev.key !== run.key) return run;
  if (ev.fill.status === 'done') {
    const filled = typeof ev.fill.filled === 'number' ? ev.fill.filled : 0;
    const credits = typeof ev.fill.credits === 'number' ? ev.fill.credits : 0;
    return { status: 'done', key: run.key, filled, credits };
  }
  if (ev.fill.status === 'failed') {
    return { status: 'failed', key: run.key, fillError: ev.fill.error ?? null, streamCode: null };
  }
  return run;
}

export function estimateToApprove(
  state: SnippetState,
  form: { label: string; prompt: string; inputs: string[] },
): Extract<Estimate, { status: 'ready' }> | null {
  if (state.phase !== 'ready' || state.run.status !== 'idle') return null;
  const { estimate } = state;
  if (estimate.status !== 'ready' || estimate.inputs !== inputsKey(form.inputs)) return null;
  if (estimate.credits < 1 || !form.label.trim() || form.prompt.trim().length < MIN_PROMPT)
    return null;
  return estimate;
}
