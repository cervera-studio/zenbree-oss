'use client';

import { Loader2, Pencil, Sparkles, Table2 } from 'lucide-react';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { type ReactNode, useReducer, useRef, useState } from 'react';
import {
  deleteCampaignAction,
  livePreviewAction,
  testEmailAction,
  transitionCampaignAction,
  updateCampaignAction,
} from '@/app/(app)/campaigns/actions';
import { AppPage } from '@/components/app/page';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { Input } from '@/components/ui/input';
import { Panel } from '@/components/ui/panel';
import { toast } from '@/components/ui/sonner';
import type { Campaign, Preview } from '@/lib/campaigns/api';
import { blockers, rewriteHref } from '@/lib/campaigns/builder';
import {
  blankFallbacks,
  type CatalogEntry,
  type FieldCatalog,
  toApiFields,
  withField,
} from '@/lib/campaigns/fields';
import type { CampaignSenders } from '@/lib/campaigns/senders';
import { hasLinkedIn, type Step, toApiSteps } from '@/lib/campaigns/steps';
import { useAction } from '@/lib/hooks/use-action';
import type { Result } from '@/lib/types';
import { BuilderBlockers, BuilderError } from './builder-parts';
import { ApprovalNote, SendWindow, StepPreview } from './builder-side';
import { BuilderSteps } from './builder-steps';
import { type Action, initialState, reducer } from './campaign-builder-state';
import { CampaignFields } from './campaign-fields';
import { CampaignHead } from './campaign-head';
import { useTokenChips } from './field-picker';
import { ParallelCampaigns } from './parallel-campaigns';
import { SenderPicker } from './sender-picker';
import { VariantTest } from './variant-test';

const PREVIEW_DELAY_MS = 500;

export function CampaignBuilder({
  campaign,
  senders,
  linkedInConnected,
  preview: items,
  catalog,
  queue,
}: {
  campaign: Campaign;
  catalog: FieldCatalog | null;
  senders: CampaignSenders | null;
  linkedInConnected: boolean;
  preview: Preview['items'];
  queue: ReactNode;
}) {
  const t = useTranslations('campaigns');
  const tb = useTranslations('campaignBuilder');
  const tc = useTranslations('common');
  const ti = useTranslations('integrations');
  const [state, dispatch] = useReducer(
    reducer,
    { steps: campaign.steps, fields: campaign.fields, items },
    initialState,
  );
  const { steps, fields, dirty, open, notice, preview, abTest } = state;
  const latestSteps = useRef(steps);
  latestSteps.current = steps;
  const latestFields = useRef(fields);
  latestFields.current = fields;
  const chips = useTokenChips(fields, catalog);
  const [person, setPerson] = useState(0);
  const [renaming, setRenaming] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const { pending, error, run: act } = useAction();
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const latest = useRef(0);
  const draft = campaign.status === 'draft';
  const unready = blankFallbacks(fields).length > 0;
  const blocked = draft
    ? blockers(steps, senders?.senders ?? null, linkedInConnected, campaign.targetCount, abTest)
    : [];

  function run<T>(action: () => Promise<Result<T> | undefined>, done?: (data: T) => void) {
    dispatch({ type: 'requestStarted' });
    act(action, { onSuccess: done });
  }

  // The preview follows what is typed (#217): one request per pause in typing, the newest wins.
  function refreshPreview() {
    clearTimeout(timer.current);
    timer.current = setTimeout(async () => {
      const ticket = ++latest.current;
      const result = await livePreviewAction(
        campaign.id,
        toApiSteps(latestSteps.current),
        toApiFields(latestFields.current),
      );
      if (ticket !== latest.current) return;
      dispatch(
        result.success
          ? { type: 'previewLoaded', items: result.data.items }
          : { type: 'previewFailed' },
      );
    }, PREVIEW_DELAY_MS);
  }

  function edit(action: Action) {
    dispatch(action);
    refreshPreview();
  }

  function pickField(entry: CatalogEntry) {
    const added = withField(latestFields.current, entry);
    if (added.fields !== latestFields.current) edit({ type: 'fieldsEdited', fields: added.fields });
    return added.token;
  }

  const fieldName = (token: string) => chips.find((c) => c.token === token)?.label ?? token;

  const notify = (text: string) => dispatch({ type: 'noticeShown', text });

  // Unsaved edit, so reversible (P1): «Annulla» puts the step back where it was.
  function removeStep(i: number, removed: Step) {
    edit({ type: 'stepRemoved', index: i });
    toast.success(t('step.removed', { number: i + 1 }), {
      action: {
        label: tc('undo'),
        onClick: () => edit({ type: 'stepRestored', index: i, step: removed }),
      },
    });
  }

  function save(then?: () => Promise<Result<Campaign>>) {
    run(
      async () => {
        const saved = await updateCampaignAction(campaign.id, {
          steps: toApiSteps(steps),
          fields: toApiFields(fields),
        });
        if (!saved.success || !then) return saved;
        dispatch({ type: 'stepsSaved' });
        return then();
      },
      (result) =>
        dispatch({
          type: 'campaignLoaded',
          steps: result.steps,
          fields: result.fields,
          notice: then ? undefined : tb('footer.saved'),
        }),
    );
  }

  function rename(value: string) {
    setRenaming(false);
    const name = value.trim();
    if (!name || name === campaign.name) return;
    run(
      () => updateCampaignAction(campaign.id, { name }),
      () => notify(tb('renamed')),
    );
  }

  const activate = () => transitionCampaignAction(campaign.id, 'activate');

  const actions = (
    <>
      <Button variant="outline" disabled={pending || !dirty || unready} onClick={() => save()}>
        {tb('footer.save')}
      </Button>
      <Button
        disabled={pending || blocked.length > 0 || unready}
        onClick={() => (dirty ? save(activate) : run(activate))}
      >
        {pending && <Loader2 className="size-4 animate-spin" />}
        {t('activate')}
      </Button>
    </>
  );

  return (
    <div className="flex min-h-full flex-col">
      <AppPage
        crumbs={[{ label: t('title'), href: '/campaigns' }, { label: campaign.name }]}
        crumbsLabel={t('breadcrumb')}
        topbarActions={draft && <div className="flex gap-2 phone:hidden">{actions}</div>}
        className="flex-1"
      >
        <CampaignHead
          title={
            renaming ? (
              <Input
                autoFocus
                aria-label={tb('renameLabel')}
                defaultValue={campaign.name}
                maxLength={200}
                className="text-title h-10 max-w-xl"
                onBlur={(e) => rename(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') e.currentTarget.blur(); // saves through onBlur, once
                  if (e.key === 'Escape') setRenaming(false);
                }}
              />
            ) : (
              <>
                <h1 className="text-title">{campaign.name}</h1>
                {draft && (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    disabled={pending}
                    onClick={() => setRenaming(true)}
                    aria-label={tb('rename')}
                  >
                    <Pencil />
                  </Button>
                )}
              </>
            )
          }
          state={
            <Badge tone="neutral" dot>
              {t(`status.${campaign.status}`)}
            </Badge>
          }
          meta={
            <>
              <p>
                {campaign.listId && campaign.listName ? (
                  <Link
                    href={`/lists/${campaign.listId}`}
                    className="hover:text-ink hover:underline"
                  >
                    <Table2
                      className="mr-1.5 inline-block size-3.5 align-text-bottom"
                      aria-hidden="true"
                    />
                    {tb.rich('source.linked', {
                      count: campaign.targetCount,
                      list: campaign.listName,
                      b: (chunks) => <strong className="text-ink font-bold">{chunks}</strong>,
                    })}
                  </Link>
                ) : (
                  tb('source.gone', { count: campaign.targetCount })
                )}
              </p>
              <p>
                {t('stats', {
                  targets: campaign.targetCount,
                  sent: campaign.sent,
                  replied: campaign.replied,
                  failed: campaign.failed,
                })}
              </p>
              {draft && blocked.length === 0 && <p>{t('activateHelp')}</p>}
            </>
          }
          actions={
            <div className="flex gap-2">
              {draft && campaign.chatSessionId && (
                <Button asChild variant="outline">
                  <Link
                    href={rewriteHref(
                      campaign.chatSessionId,
                      tb('rewrite.prompt', { name: campaign.name, id: campaign.id }),
                    )}
                  >
                    <Sparkles aria-hidden="true" />
                    {tb('rewrite.link')}
                  </Link>
                </Button>
              )}
              {draft && (
                <Button variant="ghost" disabled={pending} onClick={() => setConfirmDelete(true)}>
                  {t('delete')}
                </Button>
              )}
            </div>
          }
        />
        <ConfirmDialog
          open={confirmDelete}
          onOpenChange={setConfirmDelete}
          title={t('deleteTitle', { name: campaign.name })}
          description={t('deleteBody')}
          consequences={[t('deleteBullet1', { count: campaign.targetCount }), t('deleteBullet2')]}
          confirmLabel={t('delete')}
          onConfirm={() => run(() => deleteCampaignAction(campaign.id))}
        />
        <BuilderBlockers blocked={blocked} />
        {error && <BuilderError error={error} chips={chips} />}
        {notice && (
          <p role="status" className="text-ui">
            {notice}
          </p>
        )}

        <div className="grid grid-cols-1 items-start gap-4 desktop:grid-cols-[minmax(0,1fr)_22.5rem]">
          <div className="flex min-w-0 flex-col gap-4">
            <BuilderSteps
              steps={steps}
              open={open}
              abTest={abTest}
              draft={draft}
              pending={pending}
              chips={chips}
              catalog={catalog}
              snippetCampaignId={draft && campaign.listId ? campaign.id : null}
              onToggle={(index) => dispatch({ type: 'stepToggled', index })}
              onChange={(i, next) => edit({ type: 'stepChanged', index: i, step: next })}
              onRemove={removeStep}
              onAdd={(channel) => edit({ type: 'stepAdded', channel })}
              onPick={pickField}
              onTest={(i) =>
                steps[i]?.channel === 'email' && !dirty
                  ? (variant) =>
                      run(
                        () => testEmailAction(campaign.id, i + 1, variant),
                        (sent) => notify(t('testSent', { to: sent.to })),
                      )
                  : undefined
              }
            />
            <CampaignFields
              fields={fields}
              steps={steps}
              catalog={catalog}
              editable={draft}
              onChange={(next) => edit({ type: 'fieldsEdited', fields: next })}
            />
          </div>

          <aside className="flex flex-col gap-4">
            <Panel title={tb('senders.title')} meta={tb('senders.meta')}>
              <SenderPicker campaignId={campaign.id} pool={senders} keepOne={false} />
            </Panel>
            <VariantTest
              campaign={campaign}
              on={abTest}
              steps={steps}
              onSwitch={(on) => edit({ type: 'testSwitched', on })}
            />
            <SendWindow />
            <ApprovalNote />
            <ParallelCampaigns campaign={campaign} />
            <StepPreview
              items={preview.items}
              abTest={abTest}
              step={open !== null && open < steps.length ? open + 1 : steps.length ? 1 : null}
              person={person}
              onPerson={setPerson}
              updating={preview.status === 'loading'}
              failed={preview.status === 'failed'}
              fieldName={fieldName}
            />
            {hasLinkedIn(steps) && (
              <div className="text-small flex flex-col gap-1">
                <p className="text-warning text-ui-strong">{t('riskTitle')}</p>
                {/* The API's `linkedin_risk_notice` is Italian only: the same notice as Integrations (#699). */}
                <p className="text-muted whitespace-pre-line">{ti('linkedin.riskDefault')}</p>
              </div>
            )}
          </aside>
        </div>

        {queue}

        {draft && (
          <div className="bg-ground border-line sticky bottom-0 mt-auto flex justify-end gap-2 border-t py-3 desktop:hidden">
            {actions}
          </div>
        )}
      </AppPage>
    </div>
  );
}
