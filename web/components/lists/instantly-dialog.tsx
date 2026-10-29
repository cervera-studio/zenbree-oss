'use client';

import { Loader2 } from 'lucide-react';
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
import type { ListColumn } from '@/lib/lists/api';
import {
  type InstantlyCampaign,
  type InstantlyEnrollment,
  type InstantlyMapping,
  type InstantlyPreview,
  instantlyApi,
} from '@/lib/lists/instantly';
import type { AppErrorJSON, Result } from '@/lib/types';
import { PreviewStep, ResultStep } from './instantly-review';
import { CampaignStep, defaultMapping, MappingStep, toApiMapping } from './instantly-steps';
import { useInstantlyPolling } from './use-instantly-polling';

type Step = 'campaign' | 'mapping' | 'preview' | 'result';
type Campaigns =
  | { state: 'loading' }
  | { state: 'failed' }
  | { state: 'ready'; list: InstantlyCampaign[] };

export function InstantlyDialog({
  listId,
  rowIds,
  columns,
  onClose,
}: {
  listId: string;
  rowIds: string[];
  columns: ListColumn[];
  onClose: () => void;
}) {
  const t = useTranslations('lists.instantly');
  const tc = useTranslations('common');
  const errorText = useErrorText();
  // Fixed at open: the selection can change under the dialog, the batch cannot.
  const [ids] = useState(rowIds);
  const [step, setStep] = useState<Step>('campaign');
  const [campaigns, setCampaigns] = useState<Campaigns>({ state: 'loading' });
  const [picked, setPicked] = useState<string | null>(null);
  const [mapping, setMapping] = useState<InstantlyMapping>(() => defaultMapping(columns));
  const [preview, setPreview] = useState<InstantlyPreview | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [enrollment, setEnrollment] = useState<InstantlyEnrollment | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<AppErrorJSON | null>(null);

  const load = useCallback(async () => {
    setCampaigns({ state: 'loading' });
    const res = await instantlyApi.campaigns();
    setCampaigns(res.success ? { state: 'ready', list: res.data.campaigns } : { state: 'failed' });
    if (!res.success) setFailure(res.error);
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  // A batch the user left earlier comes first: BUSY must never have no way out.
  useEffect(() => {
    let stale = false;
    instantlyApi.latest().then((res) => {
      if (stale || !res.success || !res.data) return;
      setEnrollment(res.data);
      setStep('result');
    });
    return () => {
      stale = true;
    };
  }, []);
  useInstantlyPolling(enrollment, setEnrollment);

  const target = { listId, rowIds: ids, campaignId: picked ?? '', mapping: toApiMapping(mapping) };

  async function run<T>(call: () => Promise<Result<T>>, onDone: (data: T) => void) {
    setBusy(true);
    setFailure(null);
    const res = await call();
    setBusy(false);
    if (res.success) onDone(res.data);
    else setFailure(res.error);
  }

  const toPreview = () =>
    run(
      () => instantlyApi.preview(target),
      (data) => {
        setPreview(data);
        setAcknowledged(false);
        setStep('preview');
      },
    );
  const confirm = () =>
    run(
      () => instantlyApi.enroll(target, acknowledged),
      (data) => {
        setEnrollment(data);
        setStep('result');
      },
    );
  const update = (call: (id: string) => Promise<Result<InstantlyEnrollment>>) =>
    enrollment && run(() => call(enrollment.id), setEnrollment);

  const needsAck = Boolean(preview?.campaign.may_send) && !acknowledged;
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        aria-describedby={undefined}
        className="flex max-h-11/12 flex-col gap-0 overflow-hidden rounded-xl p-0 sm:max-w-120"
      >
        <DialogHeader className="border-hair gap-0.5 border-b px-4 py-3.5 pr-14">
          <DialogTitle className="text-feature">{t('title')}</DialogTitle>
          <p className="text-muted text-small">{t('people', { n: ids.length })}</p>
        </DialogHeader>
        <div className="flex min-h-0 flex-col gap-3 overflow-y-auto px-4 pt-4">
          {failure && (
            <p role="alert" className="bg-negative-tint text-ui rounded-lg p-3.5">
              {errorText(failure, t('error'))}
            </p>
          )}
          {step === 'campaign' && campaigns.state === 'loading' && (
            <p className="text-muted text-sm">{tc('loading')}</p>
          )}
          {step === 'campaign' && campaigns.state === 'failed' && (
            <Button variant="link" className="self-start" onClick={() => void load()}>
              {tc('retry')}
            </Button>
          )}
          {step === 'campaign' && campaigns.state === 'ready' && (
            <CampaignStep campaigns={campaigns.list} picked={picked} onPick={setPicked} />
          )}
          {step === 'mapping' && (
            <MappingStep columns={columns} mapping={mapping} onChange={setMapping} />
          )}
          {step === 'preview' && preview && (
            <PreviewStep
              preview={preview}
              acknowledged={acknowledged}
              onAcknowledge={setAcknowledged}
            />
          )}
          {step === 'result' && enrollment && <ResultStep enrollment={enrollment} />}
        </div>
        <DialogFooter className="p-4">
          {step === 'result' ? (
            <>
              {enrollment?.can_cancel && (
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() => void update(instantlyApi.cancel)}
                >
                  {t('cancel')}
                </Button>
              )}
              {enrollment?.can_retry && (
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() => void update(instantlyApi.retry)}
                >
                  {t('retry')}
                </Button>
              )}
              {enrollment && enrollment.status !== 'queued' && enrollment.status !== 'running' && (
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() => {
                    setEnrollment(null);
                    setFailure(null);
                    setStep('campaign');
                  }}
                >
                  {t('newBatch')}
                </Button>
              )}
              <Button onClick={onClose}>{tc('close')}</Button>
            </>
          ) : (
            <>
              <Button
                variant="outline"
                disabled={busy}
                onClick={
                  step === 'campaign'
                    ? onClose
                    : () => setStep(step === 'preview' ? 'mapping' : 'campaign')
                }
              >
                {step === 'campaign' ? tc('cancel') : t('back')}
              </Button>
              {step === 'campaign' && (
                <Button disabled={!picked} onClick={() => setStep('mapping')}>
                  {t('next')}
                </Button>
              )}
              {step === 'mapping' && (
                <Button disabled={busy} onClick={() => void toPreview()}>
                  {busy && <Loader2 className="animate-spin" />}
                  {t('preview')}
                </Button>
              )}
              {step === 'preview' && (
                <Button
                  disabled={busy || needsAck || !preview?.eligible}
                  onClick={() => void confirm()}
                >
                  {busy && <Loader2 className="animate-spin" />}
                  {t('confirm', { n: preview?.eligible ?? 0 })}
                </Button>
              )}
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
