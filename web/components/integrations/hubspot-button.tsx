'use client';

import { Loader2 } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { useState } from 'react';
import {
  connectHubSpotAction,
  disconnectHubSpotAction,
} from '@/app/(app)/settings/integrations/actions';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { toast } from '@/components/ui/sonner';
import { useAction } from '@/lib/hooks/use-action';

export function HubSpotButton({ connected, disabled }: { connected: boolean; disabled?: boolean }) {
  const t = useTranslations('integrations.hubspot');
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const { pending, run } = useAction();

  function connect() {
    run(connectHubSpotAction, {
      onSuccess: (url) => window.location.assign(url),
      errorToast: t('connectFailed'),
    });
  }

  async function disconnect() {
    const result = await disconnectHubSpotAction();
    if (result.success) toast.success(t('disconnectedToast'));
    else toast.error(t('disconnectFailed'));
    router.refresh();
  }

  if (!connected) {
    return (
      <Button size="sm" onClick={connect} disabled={disabled || pending}>
        {pending && <Loader2 className="animate-spin" />}
        {t('connect')}
      </Button>
    );
  }

  return (
    <>
      <Button variant="outline" size="xs" onClick={() => setOpen(true)}>
        {t('disconnect')}
      </Button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title={t('disconnectTitle')}
        description={t('disconnectDescription')}
        confirmLabel={t('disconnect')}
        onConfirm={disconnect}
      />
    </>
  );
}
