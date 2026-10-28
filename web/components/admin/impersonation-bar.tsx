'use client';

import { Eye } from 'lucide-react';
import { useNow, useTranslations } from 'next-intl';
import { useTransition } from 'react';
import { Button } from '@/components/ui/button';
import { toast } from '@/components/ui/sonner';
import { authClient } from '@/lib/auth-client';
import { minutesLeft } from './impersonation-minutes';

// Shown while an admin browses as another user (Better Auth sets session.impersonatedBy).
export function ImpersonationBar({
  name,
  admin,
  expiresAt,
}: {
  name: string;
  admin: string;
  /** ISO time the impersonated session ends. */
  expiresAt: string;
}) {
  const t = useTranslations('admin.impersonation');
  // Re-read each minute: the app layout stays mounted across navigations, so the server cannot count.
  const now = useNow({ updateInterval: 60_000 });
  const [pending, start] = useTransition();
  const exit = () =>
    start(async () => {
      const { error } = await authClient.admin.stopImpersonating();
      if (error) toast.error(t('exitFailed'));
      // Full navigation: drops client state (chat stream, cached tokens) of the impersonated user.
      // On error (e.g. the impersonation session expired) '/' lands on sign-in instead of looping.
      window.location.assign(error ? '/' : '/admin');
    });

  return (
    <div
      role="status"
      className="bg-accent-tint text-ink text-ui flex min-h-11 shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b px-5 py-1.5"
    >
      <Eye className="size-4 shrink-0" aria-hidden />
      <span className="min-w-60 flex-1">
        {t.rich('banner', {
          name,
          admin,
          b: (chunks) => <b className="font-bold">{chunks}</b>,
        })}
      </span>
      <time
        dateTime={expiresAt}
        suppressHydrationWarning
        className="text-data-sm text-soft shrink-0 font-mono"
      >
        {t('expires', { n: minutesLeft(expiresAt, now) })}
      </time>
      <Button
        size="sm"
        variant="outline"
        className="bg-raised shrink-0"
        disabled={pending}
        onClick={exit}
      >
        {t('exit')}
      </Button>
    </div>
  );
}
