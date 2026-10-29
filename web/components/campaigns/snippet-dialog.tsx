'use client';

import { Loader2 } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { type Dispatch, useEffect, useReducer, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Meter } from '@/components/ui/meter';
import { Textarea } from '@/components/ui/textarea';
import { useErrorText } from '@/i18n/error-text';
import {
  estimateToApprove,
  initialSnippetState,
  inputsKey,
  MAX_INPUTS,
  MAX_LABEL,
  MAX_PROMPT,
  type Run,
  researchColumns,
  type SnippetAction,
  type SnippetState,
  snippetReducer,
  snippetRows,
} from '@/lib/campaigns/snippet';
import { listsApi } from '@/lib/lists/api';
import { fillErrorKey } from '@/lib/lists/columns';
import { SnippetEstimate } from './snippet-estimate';
import { useSnippetStream } from './use-snippet-stream';

type Ready = Extract<SnippetState, { phase: 'ready' }>;

async function estimate(
  dispatch: Dispatch<SnippetAction>,
  listId: string,
  rowIds: string[],
  picked: string[],
) {
  if (!picked.length || !rowIds.length) return dispatch({ type: 'estimateCleared' });
  const key = inputsKey(picked);
  dispatch({ type: 'estimating', inputs: key });
  const res = await listsApi.estimateSnippet(listId, picked, rowIds);
  dispatch(
    res.success
      ? {
          type: 'estimated',
          inputs: key,
          researchRows: res.data.research_rows,
          credits: res.data.credits,
        }
      : { type: 'estimateFailed', inputs: key, error: res.error },
  );
}

export function SnippetDialog({
  campaignId,
  people,
  open,
  onOpenChange,
}: {
  campaignId: string;
  people: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useTranslations('campaignBuilder.snippet');
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-11/12 overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="text-lg font-bold">{t('title')}</DialogTitle>
          <DialogDescription>{t('description')}</DialogDescription>
        </DialogHeader>
        <SnippetForm campaignId={campaignId} people={people} onClose={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}

function SnippetForm({
  campaignId,
  people,
  onClose,
}: {
  campaignId: string;
  people: number;
  onClose: () => void;
}) {
  const t = useTranslations('campaignBuilder.snippet');
  const tc = useTranslations('common');
  const errorText = useErrorText();
  const router = useRouter();
  const [state, dispatch] = useReducer(snippetReducer, initialSnippetState);
  const [label, setLabel] = useState('');
  const [prompt, setPrompt] = useState('');
  const [inputs, setInputs] = useState<string[]>([]);
  useSnippetStream(state, dispatch);

  useEffect(() => {
    let stale = false;
    void (async () => {
      const rows = await snippetRows(campaignId);
      if (stale) return;
      const listId = rows.success ? rows.data.list_id : null;
      if (!rows.success || !listId)
        return dispatch({ type: 'loadFailed', error: rows.success ? null : rows.error });
      const list = await listsApi.get(listId);
      if (stale) return;
      if (!list.success) return dispatch({ type: 'loadFailed', error: list.error });
      const research = researchColumns(list.data.columns ?? []);
      const { row_ids: rowIds, total: onList } = rows.data;
      dispatch({
        type: 'loaded',
        setup: { listId, rowIds, onList, research, prices: list.data.prices },
      });
      const picked = research.slice(0, MAX_INPUTS).map((c) => c.key);
      setInputs(picked);
      void estimate(dispatch, listId, rowIds, picked);
    })();
    return () => {
      stale = true;
    };
  }, [campaignId]);

  if (state.phase === 'loading') {
    return <Loader2 className="text-muted mx-auto size-5 animate-spin" aria-hidden="true" />;
  }
  if (state.phase === 'unavailable') {
    return <p className="text-negative text-small">{errorText(state.error, t('unavailable'))}</p>;
  }
  const { setup, run } = state;
  const approved = estimateToApprove(state, { label, prompt, inputs });

  async function approve() {
    if (!approved) return;
    dispatch({ type: 'creating' });
    const res = await listsApi.addSnippetColumn(setup.listId, {
      label: label.trim(),
      prompt: prompt.trim(),
      inputs,
      row_ids: setup.rowIds,
      approved_credits: approved.credits,
    });
    if (!res.success) return dispatch({ type: 'createFailed', error: res.error });
    dispatch({ type: 'created', key: res.data.key, total: approved.researchRows });
    router.refresh();
  }

  if (run.status !== 'idle' && run.status !== 'creating') {
    return <SnippetRun run={run} label={label.trim()} onClose={onClose} />;
  }
  const busy = run.status === 'creating';
  return (
    <form
      className="grid gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        void approve();
      }}
    >
      <div className="grid gap-2">
        <Label htmlFor="snippet-label">{t('label')}</Label>
        <Input
          id="snippet-label"
          value={label}
          maxLength={MAX_LABEL}
          placeholder={t('labelPlaceholder')}
          onChange={(e) => setLabel(e.target.value)}
        />
      </div>
      <div className="grid gap-2">
        <Label htmlFor="snippet-prompt">{t('prompt')}</Label>
        <Textarea
          id="snippet-prompt"
          value={prompt}
          maxLength={MAX_PROMPT}
          placeholder={t('promptPlaceholder')}
          onChange={(e) => setPrompt(e.target.value)}
        />
      </div>
      <ResearchInputs
        state={state}
        inputs={inputs}
        onChange={(next) => {
          setInputs(next);
          void estimate(dispatch, setup.listId, setup.rowIds, next);
        }}
      />
      {setup.rowIds.length === 0 ? (
        <p className="text-muted text-small">{t('noPeople')}</p>
      ) : (
        <>
          <SnippetEstimate estimate={state.estimate} price={setup.prices.ai_cell} />
          {setup.onList < people || setup.rowIds.length < setup.onList ? (
            <p className="text-muted text-small">
              {t('coverage', {
                rows: setup.rowIds.length,
                people,
                max: setup.prices.ai_cells_per_run,
              })}
            </p>
          ) : null}
        </>
      )}
      {run.status === 'idle' && run.error && (
        <p role="alert" className="text-negative text-small">
          {errorText(run.error, t('createFailed'))}
        </p>
      )}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onClose}>
          {tc('cancel')}
        </Button>
        <Button type="submit" disabled={!approved || busy}>
          {busy && <Loader2 className="size-4 animate-spin" aria-hidden="true" />}
          {busy ? t('creating') : t('approve')}
        </Button>
      </DialogFooter>
    </form>
  );
}

function ResearchInputs({
  state,
  inputs,
  onChange,
}: {
  state: Ready;
  inputs: string[];
  onChange: (inputs: string[]) => void;
}) {
  const t = useTranslations('campaignBuilder.snippet');
  const { research } = state.setup;
  if (research.length === 0) return <p className="text-muted text-small">{t('noResearch')}</p>;
  const full = inputs.length >= MAX_INPUTS;
  return (
    <fieldset className="grid gap-2">
      <legend className="text-ui-strong text-ink mb-2">
        {t('inputs')}
        {research.length > MAX_INPUTS && (
          <span className="text-muted text-small font-normal">
            {' · '}
            {t('inputsMax', { max: MAX_INPUTS })}
          </span>
        )}
      </legend>
      {research.map((c) => {
        const checked = inputs.includes(c.key);
        return (
          <div key={c.key} className="flex min-h-8 items-center gap-2">
            <Checkbox
              id={`snippet-input-${c.key}`}
              checked={checked}
              disabled={!checked && full}
              onCheckedChange={(on) =>
                onChange(on ? [...inputs, c.key] : inputs.filter((k) => k !== c.key))
              }
            />
            <Label htmlFor={`snippet-input-${c.key}`} className="min-w-0 truncate font-normal">
              {c.label}
            </Label>
          </div>
        );
      })}
    </fieldset>
  );
}

function SnippetRun({
  run,
  label,
  onClose,
}: {
  run: Exclude<Run, { status: 'idle' | 'creating' }>;
  label: string;
  onClose: () => void;
}) {
  const t = useTranslations('campaignBuilder.snippet');
  const tl = useTranslations('lists');
  const tc = useTranslations('common');
  const errorText = useErrorText();
  return (
    <div className="grid gap-4">
      {run.status === 'filling' && (
        <div role="status" className="grid gap-2">
          <Meter kind="ticks" value={run.total ? run.written.length / run.total : 0} />
          <p className="text-ui text-ink tabular-nums">
            {t('progress', { done: run.written.length, people: run.total })}
          </p>
          <p className="text-muted text-small">{t('progressNote')}</p>
        </div>
      )}
      {run.status === 'done' && (
        <div role="status" className="grid gap-1">
          <p className="text-ui-strong text-ink tabular-nums">
            {t('done', { filled: run.filled, credits: run.credits })}
          </p>
          <p className="text-muted text-small">{t('doneNote', { field: label })}</p>
        </div>
      )}
      {run.status === 'failed' && (
        <p role="alert" className="text-negative text-small">
          {t('failed', {
            reason: run.streamCode
              ? errorText({ code: run.streamCode })
              : run.fillError === 'vendor_disabled'
                ? t('paused')
                : tl(fillErrorKey(run.fillError)),
          })}
        </p>
      )}
      <DialogFooter>
        <Button type="button" onClick={onClose}>
          {tc('close')}
        </Button>
      </DialogFooter>
    </div>
  );
}
