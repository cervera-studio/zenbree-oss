'use client';

import { useTranslations } from 'next-intl';
import { useEffect, useRef, useState } from 'react';
import {
  createAutomationAction,
  previewAction,
  updateAutomationAction,
} from '@/app/(app)/automations/editor-actions';
import { unreachable } from '@/lib/api/error';
import type { Automation } from '@/lib/automations/api';
import type { Definition } from '@/lib/automations/definition';
import {
  blankDraft,
  check,
  type Draft,
  fromAutomation,
  makesNewVersion,
  type Plan,
  planKey,
  saveEffects,
  type TriggerBody,
  triggerBody,
} from '@/lib/automations/draft';
import type { Choices, Preview } from '@/lib/automations/editor-api';
import { useAction } from '@/lib/hooks/use-action';
import { type AppErrorJSON, fail } from '@/lib/types';
import { BasicsFields } from './basics-fields';
import { PreviewPanel } from './preview-panel';
import { SaveButton } from './save-button';
import { type SetStep, StepFields } from './step-fields';
import { useLeaveGuard } from './use-leave-guard';

const PREVIEW_DELAY_MS = 500;

export type SavedAutomation = Pick<
  Automation,
  'id' | 'name' | 'version' | 'enabled' | 'triggerKind' | 'cadence' | 'creditCap' | 'signal'
> & { definition: Definition };

type Answer = { key: string; preview: Preview };

// A plan has one of the two campaign steps, so at most one of them warns.
function sendsOnceApproved(preview: Preview | null) {
  if (preview?.campaign?.status === 'active') return 'campaignSends';
  if (preview?.instantly?.campaign?.maySend) return 'instantlySends';
  return null;
}

// The trigger counts too: a webhook run works on one person and a signal run on one account.
const previewKey = (plan: Plan, trigger: TriggerBody) =>
  `${JSON.stringify(trigger)} ${planKey(plan)}`;

export function AutomationEditor({
  choices,
  viewerIsCreator,
  saved,
  initialPreview = null,
}: {
  choices: Choices;
  viewerIsCreator: boolean;
  saved?: SavedAutomation;
  initialPreview?: Preview | null;
}) {
  const t = useTranslations('automationEditor');
  const untouched = saved ? fromAutomation(saved, saved.definition) : blankDraft();
  const [draft, setDraft] = useState(untouched);
  const [answer, setAnswer] = useState<Answer | null>(() => {
    const plan = check(untouched).plan;
    return plan && initialPreview
      ? { key: previewKey(plan, triggerBody(untouched)), preview: initialPreview }
      : null;
  });
  const [failed, setFailed] = useState<{ key: string; error: AppErrorJSON } | null>(null);
  const [waiting, setWaiting] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const ticket = useRef(0);
  const { pending, error, run } = useAction();

  const { gaps, plan } = check(draft);
  const trigger = triggerBody(draft);
  const key = plan ? previewKey(plan, trigger) : null;
  const fresh = answer !== null && answer.key === key;
  const savedPlan = saved && { definition: saved.definition, creditCap: saved.creditCap };
  const versioned = savedPlan && plan ? makesNewVersion(savedPlan, plan) : false;
  // After a save the draft still holds fields that the plan drops, so compare what `save()` sends.
  const dirty = saved
    ? !plan ||
      versioned ||
      draft.name.trim() !== saved.name ||
      JSON.stringify(trigger) !== JSON.stringify(triggerBody(untouched))
    : JSON.stringify({ ...draft, name: draft.name.trim() }) !== JSON.stringify(untouched);
  useLeaveGuard(dirty, t('leave'));
  useEffect(() => () => clearTimeout(timer.current), []);

  function requestPreview(next: Plan | null, nextTrigger: TriggerBody, delay: number) {
    clearTimeout(timer.current);
    const nextKey = next ? previewKey(next, nextTrigger) : null;
    if (!next || !nextKey || (delay > 0 && nextKey === answer?.key)) {
      ticket.current++;
      setWaiting(null);
      return;
    }
    setWaiting(nextKey);
    const mine = ++ticket.current;
    timer.current = setTimeout(async () => {
      const result = await previewAction(next, nextTrigger, saved?.id).catch(() =>
        fail(unreachable()),
      );
      if (mine !== ticket.current) return;
      setWaiting(null);
      if (result.success) {
        setAnswer({ key: nextKey, preview: result.data });
        setFailed(null);
      } else {
        setFailed({ key: nextKey, error: result.error });
      }
    }, delay);
  }

  function update(next: Draft) {
    setDraft(next);
    requestPreview(check(next).plan, triggerBody(next), PREVIEW_DELAY_MS);
  }
  const set = (patch: Partial<Draft>) => update({ ...draft, ...patch });
  const setStep: SetStep = (kind, patch) =>
    update({ ...draft, steps: { ...draft.steps, [kind]: { ...draft.steps[kind], ...patch } } });

  const effects =
    saved && savedPlan && plan ? saveEffects({ ...saved, plan: savedPlan }, trigger, plan) : [];
  const preview = fresh ? answer.preview : null;
  // `service.update` checks the plan only when the automation stays enabled. Resuming it checks then.
  const paused = saved !== undefined && !saved.enabled;
  const blocked =
    gaps.length > 0 ||
    !preview ||
    (!paused && preview.problems.length > 0) ||
    (saved !== undefined && !dirty);

  function save() {
    if (!plan) return Promise.resolve();
    const body = { ...plan, name: draft.name.trim(), trigger };
    if (!saved) return run(() => createAutomationAction(body));
    return run(() => updateAutomationAction(saved.id, body), {
      successToast: versioned
        ? t('save.published', { version: saved.version + 1 })
        : t('save.saved'),
    });
  }

  return (
    <div className="grid items-start gap-6 desktop:grid-cols-[minmax(0,1fr)_minmax(0,24rem)]">
      <div className="flex flex-col gap-8">
        <BasicsFields
          draft={draft}
          choices={choices}
          set={set}
          watchlistDeleted={saved?.signal?.watchlistId === null}
        />
        <StepFields
          steps={draft.steps}
          choices={choices}
          viewerIsCreator={viewerIsCreator}
          setStep={setStep}
        />
      </div>
      <div className="desktop:col-start-2 desktop:row-span-2 desktop:row-start-1">
        <PreviewPanel
          plan={plan}
          gaps={gaps}
          answer={answer && { preview: answer.preview, fresh }}
          checking={waiting !== null && waiting === key}
          failed={failed && failed.key === key ? failed.error : null}
          viewerIsCreator={viewerIsCreator}
          paused={paused}
          signal={draft.trigger.kind === 'signal'}
          onPreview={() => requestPreview(plan, trigger, 0)}
        />
      </div>
      <SaveButton
        publishes={
          saved ? { version: saved.version, next: versioned ? saved.version + 1 : null } : 'create'
        }
        effects={effects}
        sends={sendsOnceApproved(preview)}
        disabled={blocked}
        pending={pending}
        error={error}
        onSave={save}
      />
    </div>
  );
}
