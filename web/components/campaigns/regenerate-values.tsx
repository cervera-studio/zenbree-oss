'use client';

import { RefreshCw } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { regenerateValuesAction } from '@/app/(app)/campaigns/actions';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { toast } from '@/components/ui/sonner';
import { useErrorText } from '@/i18n/error-text';
import { useAction } from '@/lib/hooks/use-action';

export function RegenerateValues({ campaignId }: { campaignId: string }) {
  const t = useTranslations('campaigns.detail.regenerate');
  const errorText = useErrorText();
  const { pending, run } = useAction();
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="outline" size="sm" disabled={pending} onClick={() => setOpen(true)}>
        <RefreshCw />
        {t('button')}
      </Button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        destructive={false}
        title={t('title')}
        description={t('description')}
        consequences={[t('rewritten'), t('unchanged')]}
        confirmLabel={t('button')}
        onConfirm={() =>
          run(() => regenerateValuesAction(campaignId), {
            onSuccess: (counts) => toast.success(t('done', counts)),
            errorToast: errorText,
          })
        }
      />
    </>
  );
}
