'use client';

import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { useId, useState } from 'react';
import { SettingsCard } from '@/components/settings/settings-card';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { toast } from '@/components/ui/sonner';
import { authError, useErrorText } from '@/i18n/error-text';
import { authClient } from '@/lib/auth-client';

export function DeleteAccount() {
  const t = useTranslations('settings.deleteAccount');
  const errorText = useErrorText();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState('');
  const word = t('word');
  const titleId = useId();

  async function remove() {
    try {
      const { error } = await authClient.deleteUser();
      if (error) {
        toast.error(errorText(authError(error), t('error')));
        return;
      }
    } catch {
      toast.error(t('error'));
      return;
    }
    toast.success(t('done'));
    router.replace('/auth/sign-in');
  }

  return (
    <section aria-labelledby={titleId}>
      <SettingsCard tone="danger" className="gap-2">
        <h3 id={titleId} className="text-ui-strong text-negative">
          {t('title')}
        </h3>
        <p className="text-small text-soft">{t('description')}</p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="border-negative text-negative mt-1 self-start"
          onClick={() => {
            setTyped('');
            setOpen(true);
          }}
        >
          {t('action')}
        </Button>
      </SettingsCard>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title={t('dialogTitle')}
        description={t('dialogDescription')}
        consequences={[t('keeps'), t('senders'), t('billing')]}
        confirmLabel={t('confirm')}
        confirmDisabled={typed.trim() !== word}
        onConfirm={remove}
      >
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="delete-account-word">{t('wordLabel', { word })}</Label>
          <Input
            id="delete-account-word"
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            autoComplete="off"
            spellCheck={false}
          />
        </div>
      </ConfirmDialog>
    </section>
  );
}
