'use client';

import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { useOptimistic, useTransition } from 'react';
import { setSenderPausedAction } from '@/app/(app)/settings/senders/actions';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { toast } from '@/components/ui/sonner';
import { type SenderReason, type SenderState, senderTone } from '@/lib/integrations/sender-state';

export function SenderControls({
  senderId,
  address,
  paused,
  state,
  reason,
}: {
  senderId: string;
  address: string;
  paused: boolean;
  state: SenderState;
  reason: SenderReason | null | undefined;
}) {
  const t = useTranslations('senders');
  const [pending, startTransition] = useTransition();
  const [optimistic, setOptimistic] = useOptimistic(paused);

  function toggle() {
    const pause = !optimistic;
    startTransition(async () => {
      setOptimistic(pause);
      const result = await setSenderPausedAction(senderId, pause);
      if (result.success) toast.success(t(pause ? 'pausedToast' : 'resumedToast'));
      else toast.error(t('pauseFailed'));
    });
  }

  const shown = optimistic ? 'paused' : reason === 'paused' ? null : reason;

  if (reason === 'needs_reconnect') {
    return (
      <>
        <Badge tone="warning" dot>
          {t('reason.needs_reconnect')}
        </Badge>
        <Button asChild size="sm">
          <Link href="/settings/integrations">{t('reconnect')}</Link>
        </Button>
      </>
    );
  }
  return (
    <>
      <Badge tone={senderTone(shown ? state : 'ready', shown)} dot>
        {shown ? t(`reason.${shown}`) : t('ready')}
      </Badge>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={pending}
        aria-label={t(optimistic ? 'resumeLabel' : 'pauseLabel', { address })}
        onClick={toggle}
      >
        {t(optimistic ? 'resume' : 'pause')}
      </Button>
    </>
  );
}
