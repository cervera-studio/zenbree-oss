'use client';

import { Trash2 } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { deleteAutomationAction } from '@/app/(app)/automations/editor-actions';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { useErrorText } from '@/i18n/error-text';
import { useAction } from '@/lib/hooks/use-action';

export function DeleteAutomation({ id, name }: { id: string; name: string }) {
  const t = useTranslations('automationEditor.delete');
  const errorText = useErrorText();
  const { error, run } = useAction();
  const [open, setOpen] = useState(false);
  return (
    <section className="border-line flex flex-col items-start gap-2 border-t pt-6">
      <Button variant="outline" className="text-negative phone:h-11" onClick={() => setOpen(true)}>
        <Trash2 aria-hidden />
        {t('button')}
      </Button>
      {error && (
        <p role="alert" className="text-negative text-ui">
          {errorText(error)}
        </p>
      )}
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title={t('title', { name })}
        description={t('description')}
        consequences={[t('runs'), t('running'), t('people')]}
        confirmLabel={t('button')}
        onConfirm={() => run(() => deleteAutomationAction(id))}
      />
    </section>
  );
}
