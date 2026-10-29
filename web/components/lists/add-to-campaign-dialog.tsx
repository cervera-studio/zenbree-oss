'use client';

import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useErrorText } from '@/i18n/error-text';
import {
  type AddResult,
  addToCampaign,
  BULK_MAX,
  bulkApi,
  type DraftCampaign,
  SKIP_NEXT,
  type SkipReason,
} from '@/lib/lists/bulk';
import type { RowTab } from '@/lib/lists/view';
import type { AppErrorJSON } from '@/lib/types';

const OPTION =
  'has-checked:border-accent has-checked:bg-accent-tint border-line bg-ground has-focus-visible:ring-accent text-ui flex cursor-pointer items-center gap-2.5 rounded-lg border px-3 py-2.5 has-focus-visible:ring-2';
const REASONS = Object.keys(SKIP_NEXT) as SkipReason[];
// The campaign changed under the dialog: its draft list is stale.
const STALE_CAMPAIGN = new Set(['CAMPAIGN.NOT_DRAFT', 'CAMPAIGN.NOT_FOUND']);

type Drafts =
  | { state: 'loading' }
  | { state: 'failed' }
  | { state: 'ready'; list: DraftCampaign[] };
type Done = { campaign: DraftCampaign; result: AddResult; error: AppErrorJSON | null };

/** Adds the people of the checked rows to a draft campaign of the active project, then says what happened to each. */
export function AddToCampaignDialog({
  listId,
  rowIds,
  onClose,
  onAdded,
  onShowTab,
}: {
  listId: string;
  rowIds: string[];
  onClose: () => void;
  /** Every request went through (`failed` false: the page clears the selection) or some did; the page
   * refetches the rows that are gone. */
  onAdded: (result: AddResult, failed: boolean) => void;
  onShowTab: (tab: RowTab) => void;
}) {
  const t = useTranslations('lists.bulk');
  const tc = useTranslations('common');
  const errorText = useErrorText();
  // Fixed at open: the selection clears once rows are added, and the result still names their count.
  const [ids] = useState(rowIds);
  const [drafts, setDrafts] = useState<Drafts>({ state: 'loading' });
  const [picked, setPicked] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [failure, setFailure] = useState<AppErrorJSON | null>(null);
  const [done, setDone] = useState<Done | null>(null);

  const load = useCallback(async () => {
    setDrafts({ state: 'loading' });
    const res = await bulkApi.draftCampaigns();
    setDrafts(res.success ? { state: 'ready', list: res.data } : { state: 'failed' });
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const campaign = drafts.state === 'ready' ? drafts.list.find((c) => c.id === picked) : undefined;
  const submit = async () => {
    if (!campaign) return;
    setSending(true);
    setFailure(null);
    const res = await addToCampaign(listId, campaign.id, ids);
    setSending(false);
    if (res.error && res.unsent.length === ids.length) {
      setFailure(res.error);
      if (STALE_CAMPAIGN.has(res.error.code)) {
        setPicked(null);
        void load();
      }
      return;
    }
    setDone({ campaign, result: res.result, error: res.error });
    onAdded(res.result, res.error !== null);
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        aria-describedby={undefined}
        className="flex max-h-11/12 flex-col gap-0 overflow-hidden rounded-xl p-0 sm:max-w-120"
      >
        <DialogHeader className="border-hair gap-0.5 border-b px-4 py-3.5 pr-14">
          <DialogTitle className="text-feature">{t('addToCampaign')}</DialogTitle>
          <p className="text-muted text-small">{t('people', { n: ids.length })}</p>
        </DialogHeader>
        {done ? (
          <AddResultView
            {...done}
            errorText={done.error && errorText(done.error, t('addError'))}
            onShowTab={(tab) => {
              onShowTab(tab);
              onClose();
            }}
            onClose={onClose}
          />
        ) : (
          <>
            <DraftPicker
              listId={listId}
              drafts={drafts}
              picked={picked}
              onPick={setPicked}
              onRetry={() => void load()}
              failure={failure && errorText(failure, t('addError'))}
            />
            <DialogFooter className="p-4">
              <Button variant="outline" onClick={onClose}>
                {tc('cancel')}
              </Button>
              <Button disabled={!campaign || sending} onClick={submit}>
                {t('submit')}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

function DraftPicker({
  listId,
  drafts,
  picked,
  onPick,
  onRetry,
  failure,
}: {
  listId: string;
  drafts: Drafts;
  picked: string | null;
  onPick: (id: string) => void;
  onRetry: () => void;
  failure: string | null;
}) {
  const t = useTranslations('lists.bulk');
  const tc = useTranslations('common');
  return (
    <div className="flex min-h-0 flex-col gap-3 overflow-y-auto px-4 pt-4">
      {failure && (
        <p role="alert" className="bg-negative-tint text-ui rounded-lg p-3.5">
          {failure}
        </p>
      )}
      {drafts.state === 'loading' && <p className="text-muted text-sm">{tc('loading')}</p>}
      {drafts.state === 'failed' && (
        <p role="alert" className="text-ui flex items-center gap-2">
          {t('loadError')}
          <Button variant="link" onClick={onRetry}>
            {tc('retry')}
          </Button>
        </p>
      )}
      {drafts.state === 'ready' && drafts.list.length === 0 && (
        <p className="text-ui flex flex-col items-start gap-1">
          {t('noDrafts')}
          <Link
            href={`/campaigns?list=${encodeURIComponent(listId)}`}
            className="text-accent-ink font-semibold"
          >
            {t('createCampaign')}
          </Link>
        </p>
      )}
      {drafts.state === 'ready' && drafts.list.length > 0 && (
        <fieldset className="flex flex-col gap-1.5">
          <legend className="text-small mb-2">{t('campaignLegend')}</legend>
          {drafts.list.map((c) => (
            <label key={c.id} className={OPTION}>
              <input
                type="radio"
                name="bulk-campaign"
                className="accent-accent size-4 shrink-0"
                checked={picked === c.id}
                onChange={() => onPick(c.id)}
              />
              <span className="min-w-0 grow break-words">{c.name}</span>
            </label>
          ))}
        </fieldset>
      )}
    </div>
  );
}

/** What the add did: the people added, then each reason that left some out and what to do about it. */
export function AddResultView({
  campaign,
  result,
  errorText,
  onShowTab,
  onClose,
}: {
  campaign: DraftCampaign;
  result: AddResult;
  /** A later request failed: the result counts the ones before it. */
  errorText: string | null;
  onShowTab: (tab: RowTab) => void;
  onClose: () => void;
}) {
  const t = useTranslations('lists.bulk');
  const tc = useTranslations('common');
  const reasons = REASONS.filter((r) => (result.skipped[r] ?? 0) > 0);
  return (
    <>
      <div role="status" className="flex min-h-0 flex-col gap-3 overflow-y-auto px-4 pt-4">
        <strong className="text-feature break-words">
          {t('added', { n: result.added, name: campaign.name })}
        </strong>
        {reasons.length > 0 && (
          <ul className="flex flex-col gap-2">
            {reasons.map((reason) => (
              <SkipLine
                key={reason}
                reason={reason}
                n={result.skipped[reason] ?? 0}
                onShowTab={onShowTab}
              />
            ))}
          </ul>
        )}
        {errorText && (
          <p role="alert" className="bg-negative-tint text-ui rounded-lg p-3.5">
            {errorText}
          </p>
        )}
      </div>
      <DialogFooter className="p-4">
        <Button variant="outline" asChild>
          <Link href={`/campaigns/${encodeURIComponent(campaign.id)}`}>{t('openCampaign')}</Link>
        </Button>
        <Button onClick={onClose}>{tc('close')}</Button>
      </DialogFooter>
    </>
  );
}

function SkipLine({
  reason,
  n,
  onShowTab,
}: {
  reason: SkipReason;
  n: number;
  onShowTab: (tab: RowTab) => void;
}) {
  const t = useTranslations('lists.bulk');
  const next = SKIP_NEXT[reason];
  return (
    <li className="text-ui flex flex-col items-start gap-1 rounded-lg border px-3 py-2.5">
      <span>{t(`skip.${reason}`, { n, max: BULK_MAX })}</span>
      {next && 'href' in next && (
        <Link href={next.href} className="text-accent-ink text-ui-strong">
          {t(next.action)}
        </Link>
      )}
      {next && 'tab' in next && (
        <Button variant="link" size="xs" onClick={() => onShowTab(next.tab)}>
          {t(next.action)}
        </Button>
      )}
    </li>
  );
}
