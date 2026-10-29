'use client';

import { useTranslations } from 'next-intl';
import { useEffect, useState } from 'react';
import { ResultStep } from '@/components/lists/instantly-review';
import { useInstantlyPolling } from '@/components/lists/use-instantly-polling';
import { Button } from '@/components/ui/button';
import { useErrorText } from '@/i18n/error-text';
import { type InstantlyEnrollment, instantlyApi } from '@/lib/lists/instantly';
import type { AppErrorJSON, Result } from '@/lib/types';

/** The newest batch that needs a decision, with the controls that end it. Nothing shows when none does. */
export function InstantlyBatch({ connected }: { connected: boolean }) {
  const t = useTranslations('integrations.instantly');
  const tl = useTranslations('lists.instantly');
  const errorText = useErrorText();
  const [batch, setBatch] = useState<InstantlyEnrollment | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<AppErrorJSON | null>(null);

  useEffect(() => {
    let stale = false;
    instantlyApi.latest().then((res) => {
      if (!stale && res.success) setBatch(res.data);
    });
    return () => {
      stale = true;
    };
  }, []);
  useInstantlyPolling(batch, setBatch);

  async function act(call: (id: string) => Promise<Result<InstantlyEnrollment>>) {
    if (!batch) return;
    setBusy(true);
    setFailure(null);
    const res = await call(batch.id);
    setBusy(false);
    if (res.success) setBatch(res.data);
    else setFailure(res.error);
  }

  if (!batch) return null;
  return (
    <div className="flex flex-col gap-3 border-t pt-3">
      <h4 className="text-ui-strong">{t('latestBatch')}</h4>
      {failure && (
        <p role="alert" className="bg-negative-tint text-ui rounded-lg p-3.5">
          {errorText(failure, tl('error'))}
        </p>
      )}
      {!connected && <p className="bg-warning-tint text-ui rounded-lg p-3.5">{t('heldNote')}</p>}
      <ResultStep enrollment={batch} />
      <div className="flex flex-wrap gap-2">
        {batch.can_cancel && (
          <Button variant="outline" disabled={busy} onClick={() => void act(instantlyApi.cancel)}>
            {tl('cancel')}
          </Button>
        )}
        {batch.can_retry && (
          <Button variant="outline" disabled={busy} onClick={() => void act(instantlyApi.retry)}>
            {tl('retry')}
          </Button>
        )}
      </div>
    </div>
  );
}
