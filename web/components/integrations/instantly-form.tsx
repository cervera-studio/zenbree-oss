'use client';

import { Loader2 } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { type FormEvent, useState } from 'react';
import {
  connectInstantlyAction,
  disconnectInstantlyAction,
} from '@/app/(app)/settings/integrations/actions';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { toast } from '@/components/ui/sonner';
import { useErrorText } from '@/i18n/error-text';
import { useAction } from '@/lib/hooks/use-action';

// The key lives in this component's state only until the submit; it is never rendered back.
export function InstantlyForm({ connected, disabled }: { connected: boolean; disabled: boolean }) {
  const t = useTranslations('integrations.instantly');
  const errorText = useErrorText();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [apiKey, setApiKey] = useState('');
  const { pending, error, run } = useAction();

  function submit(event: FormEvent) {
    event.preventDefault();
    run(() => connectInstantlyAction({ name, apiKey }), {
      onSuccess: () => {
        setApiKey('');
        toast.success(t('connectedToast'));
        router.refresh();
      },
    });
  }

  async function disconnect() {
    const result = await disconnectInstantlyAction();
    if (result.success) toast.success(t('disconnectedToast'));
    else toast.error(t('disconnectFailed'));
    router.refresh();
  }

  if (connected) {
    return (
      <>
        <div>
          <Button variant="outline" size="xs" disabled={disabled} onClick={() => setOpen(true)}>
            {t('disconnect')}
          </Button>
        </div>
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

  return (
    <form onSubmit={submit} className="flex flex-col gap-2.5">
      <FieldGroup className="gap-2.5">
        <Field>
          <FieldLabel htmlFor="instantly-name-input">{t('nameLabel')}</FieldLabel>
          <Input
            id="instantly-name-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={100}
            required
          />
        </Field>
        <Field>
          <FieldLabel htmlFor="instantly-key-input">{t('keyLabel')}</FieldLabel>
          <Input
            id="instantly-key-input"
            type="password"
            autoComplete="off"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            maxLength={500}
            required
          />
          <FieldDescription>{t('keyHint')}</FieldDescription>
        </Field>
      </FieldGroup>
      {error && (
        <p role="alert" className="text-small text-negative">
          {errorText(error)}
        </p>
      )}
      <div>
        <Button type="submit" size="sm" disabled={disabled || pending || !name || !apiKey}>
          {pending && <Loader2 className="animate-spin" />}
          {t('connect')}
        </Button>
      </div>
    </form>
  );
}
