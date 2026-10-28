'use client';

import { Loader2, Plus } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { useState } from 'react';
import {
  connectMailboxAction,
  disconnectMailboxAction,
} from '@/app/(app)/settings/integrations/actions';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { toast } from '@/components/ui/sonner';
import { useAction } from '@/lib/hooks/use-action';
import type { MailboxProvider } from '@/lib/integrations/schemas';

type MailboxButtonProps = { provider: MailboxProvider; disabled?: boolean } & (
  | { mode: 'disconnect'; senderId: string }
  | { mode: 'reconnect' }
  | { mode: 'add'; another: boolean }
);

export function MailboxButton(props: MailboxButtonProps) {
  const { provider, disabled } = props;
  const t = useTranslations('integrations.mailbox');
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const { pending, run } = useAction();

  function connect() {
    run(() => connectMailboxAction(provider), {
      onSuccess: (url) => window.location.assign(url),
      errorToast: t('connectFailed'),
    });
  }

  async function disconnect(senderId: string) {
    const result = await disconnectMailboxAction(senderId);
    if (result.success) toast.success(t('disconnectedToast'));
    else toast.error(t('disconnectFailed'));
    router.refresh();
  }

  if (props.mode === 'add') {
    return (
      <Button
        variant="link"
        className="h-auto min-h-9 max-w-full py-1.5 text-left whitespace-normal"
        onClick={connect}
        disabled={disabled || pending}
      >
        {pending ? (
          <Loader2 aria-hidden="true" className="animate-spin" />
        ) : (
          <Plus aria-hidden="true" />
        )}
        <span className="min-w-0">
          {t.rich(props.another ? 'addAnother' : 'addFirst', {
            provider: (name) => <span className="text-muted font-normal">{name}</span>,
            name: t(`providers.${provider}`),
          })}
        </span>
      </Button>
    );
  }

  if (props.mode === 'reconnect') {
    return (
      <Button size="xs" onClick={connect} disabled={disabled || pending}>
        {pending && <Loader2 className="animate-spin" />}
        {t('reconnect')}
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
        consequences={[t('disconnectBullet1'), t('disconnectBullet2')]}
        confirmLabel={t('disconnect')}
        onConfirm={() => disconnect(props.senderId)}
      />
    </>
  );
}
