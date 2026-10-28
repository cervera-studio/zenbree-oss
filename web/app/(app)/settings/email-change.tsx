'use client';

import { Loader2 } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { type FormEvent, useState, useTransition } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { authClient } from '@/lib/auth-client';

/** Asks Better Auth to change the address: the new one gets a verification link and nothing changes until then. */
export function EmailChange({ email }: { email: string }) {
  const t = useTranslations('settings.account');
  const [value, setValue] = useState('');
  const [status, setStatus] = useState<'idle' | 'sent' | 'error'>('idle');
  const [pending, startTransition] = useTransition();
  const next = value.trim();

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    startTransition(async () => {
      const { error } = await authClient
        .changeEmail({ newEmail: next, callbackURL: '/settings' })
        .catch(() => ({ error: true }));
      setStatus(error ? 'error' : 'sent');
      if (!error) setValue('');
    });
  }

  return (
    <form className="border-hair mt-1 flex flex-col gap-1.5 border-t pt-3.5" onSubmit={submit}>
      <label htmlFor="profile-new-email" className="text-small text-soft">
        {t('newEmail')}
      </label>
      <div className="flex gap-2">
        <Input
          id="profile-new-email"
          type="email"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          maxLength={254}
          autoComplete="off"
        />
        <Button
          type="submit"
          variant="outline"
          disabled={pending || next === '' || next.toLowerCase() === email.toLowerCase()}
        >
          {pending && <Loader2 className="size-4 animate-spin" />}
          {t('changeEmail')}
        </Button>
      </div>
      {status === 'sent' && (
        <p role="status" className="text-small text-positive">
          {t('emailChangeSent')}
        </p>
      )}
      {status === 'error' && (
        <p role="alert" className="text-small text-negative">
          {t('emailChangeError')}
        </p>
      )}
    </form>
  );
}
