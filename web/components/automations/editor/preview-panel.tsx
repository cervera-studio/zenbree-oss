'use client';

import { Loader2, RefreshCw, TriangleAlert } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { Panel } from '@/components/ui/panel';
import { useErrorText } from '@/i18n/error-text';
import { type DefinitionStep, isSeniority } from '@/lib/automations/definition';
import type { Gap, Plan } from '@/lib/automations/draft';
import type { Preview } from '@/lib/automations/editor-api';
import { everyRunStepCode, failsEveryRun } from '@/lib/integrations/instantly-campaign';
import type { AppErrorJSON } from '@/lib/types';
import { useInstantlyStatus, useInstantlyStepText } from '../labels';
import { PreviewSignals } from './preview-signals';

export type PreviewView = {
  plan: Plan | null;
  gaps: Gap[];
  answer: { preview: Preview; fresh: boolean } | null;
  checking: boolean;
  failed: AppErrorJSON | null;
  viewerIsCreator: boolean;
  paused: boolean;
  signal: boolean;
  onPreview: () => void;
};

export function PreviewPanel(view: PreviewView) {
  const t = useTranslations('automationEditor.preview');
  const { plan, answer, checking } = view;
  return (
    <Panel
      title={t('title')}
      action={
        <Button
          variant="outline"
          size="sm"
          className="phone:h-11"
          disabled={!plan || checking}
          onClick={view.onPreview}
        >
          {checking ? <Loader2 aria-hidden className="animate-spin" /> : <RefreshCw aria-hidden />}
          {t('run')}
        </Button>
      }
    >
      <div className="flex flex-col gap-5">
        <p className="text-small text-muted">{t('readsOnly')}</p>
        <Gaps gaps={view.gaps} />
        <p role="status" className="text-small text-soft empty:hidden">
          {checking ? t('checking') : !plan ? '' : !answer ? t('notYet') : ''}
        </p>
        {view.failed && !checking && <Failed error={view.failed} />}
        {plan && answer && !view.failed && (
          <div
            aria-busy={!answer.fresh || undefined}
            className={answer.fresh ? 'flex flex-col gap-5' : 'flex flex-col gap-5 opacity-60'}
          >
            <Problems
              preview={answer.preview}
              viewerIsCreator={view.viewerIsCreator}
              paused={view.paused}
            />
            {answer.preview.signals && <PreviewSignals signals={answer.preview.signals} />}
            <Records preview={answer.preview} signal={view.signal} />
            <Conditions steps={plan.definition.steps} />
            <Actions preview={answer.preview} />
            <Budget preview={answer.preview} />
          </div>
        )}
      </div>
    </Panel>
  );
}

function Gaps({ gaps }: { gaps: Gap[] }) {
  const t = useTranslations('automationEditor.gaps');
  if (gaps.length === 0) return null;
  return (
    <section className="flex flex-col gap-1.5">
      <h3 className="text-ui-strong">{t('title')}</h3>
      <ul className="text-ui flex list-disc flex-col gap-1 pl-4.5">
        {gaps.map((gap) => (
          <li key={gap}>{t(gap)}</li>
        ))}
      </ul>
    </section>
  );
}

function Failed({ error }: { error: AppErrorJSON }) {
  const t = useTranslations('automationEditor.preview');
  const errorText = useErrorText();
  return (
    <p role="alert" className="text-negative text-ui">
      {errorText(error, t('failed'))}
    </p>
  );
}

function Problems({
  preview,
  viewerIsCreator,
  paused,
}: {
  preview: Preview;
  viewerIsCreator: boolean;
  paused: boolean;
}) {
  const t = useTranslations('automationEditor.preview');
  const errorText = useErrorText();
  if (preview.problems.length === 0) {
    // Not a problem for the API, but every run fails until the connection is fixed: the warning below says so.
    if (failsEveryRun(preview.instantly?.unreadable)) return null;
    return <p className="text-positive text-ui">{t('ready')}</p>;
  }
  return (
    <section role="alert" className="flex flex-col gap-1.5">
      <h3 className="text-negative text-ui-strong">
        {paused ? t('problemsPaused') : t('problems')}
      </h3>
      <ul className="text-negative text-ui flex list-disc flex-col gap-1 pl-4.5">
        {preview.problems.map((p) => (
          <li key={p.code}>
            {errorText({ code: p.code })}
            {p.code === 'AUTOMATION.HUBSPOT_OFF' && !viewerIsCreator && ` ${t('hubspotOfCreator')}`}
          </li>
        ))}
      </ul>
      {paused && <p className="text-small text-muted">{t('pausedSaves')}</p>}
    </section>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-soft">{label}</dt>
      <dd className="text-ink font-mono tabular-nums">{value}</dd>
    </div>
  );
}

function Records({ preview, signal }: { preview: Preview; signal: boolean }) {
  const t = useTranslations('automationEditor.preview.records');
  const rows: [string, number | null][] = [
    [t('people'), preview.people],
    [t('withEmail'), preview.withEmail],
    [t('withContact'), preview.withContact],
    [t('accounts'), preview.accounts],
  ];
  const shown = rows.filter((r): r is [string, number] => r[1] !== null);
  if (shown.length === 0) return null;
  return (
    <section className="flex flex-col gap-1.5">
      <h3 className="text-ui-strong">{t('title')}</h3>
      <dl className="text-ui flex flex-col gap-1">
        {shown.map(([label, n]) => (
          <Row key={label} label={label} value={t('count', { n })} />
        ))}
      </dl>
      {preview.accounts !== null && (
        <p className="text-small text-muted">
          {signal ? t('accountsNoteSignal') : t('accountsNote', { n: preview.accountsPerRun })}
        </p>
      )}
    </section>
  );
}

function Conditions({ steps }: { steps: DefinitionStep[] }) {
  const t = useTranslations('automationEditor.preview.conditions');
  const ts = useTranslations('automationEditor.seniority');
  const items = steps.flatMap((step) => {
    if (step.type === 'find_people') {
      const seniorities = step.seniorities.map((s) => (isSeniority(s) ? ts(s) : s));
      return [
        t('titles', { titles: step.titles.join(', ') }),
        seniorities.length > 0
          ? t('seniorities', { seniorities: seniorities.join(', ') })
          : t('anySeniority'),
        t('perAccount', { n: step.perAccount }),
      ];
    }
    if (step.type === 'qualify') return [t('minScore', { n: step.minScore })];
    return [];
  });
  if (items.length === 0) return null;
  return (
    <section className="flex flex-col gap-1.5">
      <h3 className="text-ui-strong">{t('title')}</h3>
      <ul className="text-ui flex list-disc flex-col gap-1 pl-4.5">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </section>
  );
}

function Actions({ preview }: { preview: Preview }) {
  const t = useTranslations('automationEditor.preview');
  const tk = useTranslations('automations.step.kind');
  const tc = useTranslations('campaigns.status');
  const instantlyStatus = useInstantlyStatus();
  const campaign = preview.campaign;
  const instantly = preview.instantly;
  const target = (type: Preview['steps'][number]['type']) => {
    if (type === 'add_to_campaign' && campaign)
      return ` · ${campaign.name} (${tc(campaign.status)})`;
    if (type === 'add_to_instantly' && instantly?.campaign) {
      return ` · ${instantly.campaign.name} (${instantlyStatus(instantly.campaign.status)})`;
    }
    return '';
  };
  return (
    <section className="flex flex-col gap-1.5">
      <h3 className="text-ui-strong">{t('actions')}</h3>
      <ol className="text-ui flex list-decimal flex-col gap-1 pl-4.5">
        {preview.steps.map((step) => (
          <li key={step.type}>
            <span className="flex flex-wrap items-baseline justify-between gap-x-3">
              <span className="min-w-0 break-words">
                {tk(step.type)}
                {target(step.type)}
              </span>
              <span className="text-soft font-mono tabular-nums">
                {step.creditsEach === 0 ? t('free') : t('each', { n: step.creditsEach })}
              </span>
            </span>
          </li>
        ))}
      </ol>
      {campaign?.status === 'active' && <Warning text={t('activeCampaign')} />}
      {instantly?.campaign?.maySend && <Warning text={t('instantlySends')} />}
      {instantly?.unreadable && <InstantlyUnreadable code={instantly.unreadable} />}
    </section>
  );
}

// A read that failed once is retried by the run; a refused or undecryptable key fails every run until it is fixed,
// and the preview says it as the run will.
function InstantlyUnreadable({ code }: { code: string }) {
  const t = useTranslations('automationEditor.preview');
  const instantlyText = useInstantlyStepText();
  const stepCode = everyRunStepCode(code);
  if (stepCode) return <Warning text={instantlyText(stepCode, code)} />;
  return <p className="text-small text-muted">{t('instantlyUnreadable')}</p>;
}

function Warning({ text }: { text: string }) {
  return (
    <p className="bg-warning-tint text-warning text-ui flex gap-2 rounded-md p-3">
      <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
      {text}
    </p>
  );
}

function Budget({ preview }: { preview: Preview }) {
  const t = useTranslations('automationEditor.preview');
  return (
    <section className="flex flex-col gap-1.5">
      <h3 className="text-ui-strong">{t('budget')}</h3>
      <dl className="text-ui flex flex-col gap-1">
        <Row label={t('perRun')} value={t('people', { n: preview.peoplePerRun })} />
        <Row label={t('maxCredits')} value={t('credits', { n: preview.maxCredits })} />
      </dl>
    </section>
  );
}
