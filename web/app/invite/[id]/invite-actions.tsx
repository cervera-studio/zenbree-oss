'use client';

import { Loader2 } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { useState, useTransition } from 'react';
import { signInPath } from '@/app/auth/sign-in/params';
import { Button } from '@/components/ui/button';
import { authClient, signOut } from '@/lib/auth-client';

export function InviteActions({ id, valid }: { id: string; valid: boolean }) {
  const t = useTranslations('team.invitation');
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [outcome, setOutcome] = useState<'declined' | 'failed' | null>(null);
  const switchAccount = () => signOut().then(() => router.push(signInPath(`/invite/${id}`)));

  // Accepting makes the organization the active one: a full load so every page reads it.
  const accept = () =>
    startTransition(async () => {
      const { error } = await authClient.organization.acceptInvitation({ invitationId: id });
      if (error) setOutcome('failed');
      else window.location.assign('/');
    });
  const decline = () =>
    startTransition(async () => {
      const { error } = await authClient.organization.rejectInvitation({ invitationId: id });
      setOutcome(error ? 'failed' : 'declined');
    });

  if (outcome === 'declined' || !valid) {
    return (
      <div className="flex w-full flex-col gap-3">
        {outcome === 'declined' && <p role="status">{t('declined')}</p>}
        <Button asChild size="lg">
          <Link href="/">{t('home')}</Link>
        </Button>
        {!valid && (
          <Button size="lg" variant="outline" onClick={switchAccount}>
            {t('switchAccount')}
          </Button>
        )}
      </div>
    );
  }
  return (
    <div className="flex w-full flex-col gap-3">
      {outcome === 'failed' && (
        <p role="alert" className="text-negative text-sm font-semibold">
          {t('failed')}
        </p>
      )}
      <Button size="lg" disabled={pending} onClick={accept}>
        {pending && <Loader2 className="size-4 animate-spin" />}
        {t('accept')}
      </Button>
      <Button size="lg" variant="outline" disabled={pending} onClick={decline}>
        {t('decline')}
      </Button>
    </div>
  );
}
