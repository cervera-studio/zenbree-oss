'use client';

import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { disconnectLinkedInAction } from '@/app/(app)/settings/integrations/actions';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { toast } from '@/components/ui/sonner';

export function LinkedInDisconnect() {
  const t = useTranslations('integrations.linkedin');
  const router = useRouter();
  const [open, setOpen] = useState(false);

  async function disconnect() {
    const result = await disconnectLinkedInAction();
    if (result.success) toast.success(t('disconnectedToast'));
    else toast.error(t('disconnectFailed'));
    router.refresh();
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
