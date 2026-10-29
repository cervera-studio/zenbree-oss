'use client';

import { RefreshCw, Trash2 } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { deleteSegmentAction, refreshSegmentAction } from '@/app/(app)/segments/actions';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { toast } from '@/components/ui/sonner';
import { useErrorText } from '@/i18n/error-text';
import { useAction } from '@/lib/hooks/use-action';
import { PAUSED } from './preview-panel';

export function SegmentControls({
  id,
  name,
  published,
}: {
  id: string;
  name: string;
  published: boolean;
}) {
  const t = useTranslations('segments.controls');
  const errorText = useErrorText();
  const { pending, run } = useAction();
  const [deleting, setDeleting] = useState(false);
  return (
    <div className="flex flex-wrap gap-2">
      {published && (
        <Button
          variant="outline"
          className="phone:h-11"
          disabled={pending}
          onClick={() =>
            run(() => refreshSegmentAction(id), {
              successToast: t('refreshed'),
              onError: (e) => (e.code === PAUSED ? toast : toast.error)(errorText(e)),
            })
          }
        >
          <RefreshCw aria-hidden className={pending ? 'animate-spin' : undefined} />
          {t('refresh')}
        </Button>
      )}
      <Button
        variant="outline"
        className="text-negative phone:h-11"
        onClick={() => setDeleting(true)}
      >
        <Trash2 aria-hidden />
        {t('delete')}
      </Button>
      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title={t('deleteTitle', { name })}
        description={t('deleteDescription')}
        confirmLabel={t('delete')}
        onConfirm={() => run(() => deleteSegmentAction(id), { errorToast: (e) => errorText(e) })}
      />
    </div>
  );
}
