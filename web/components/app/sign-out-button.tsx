'use client';

import { LogOut } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { signOut } from '@/lib/auth-client';

export function useSignOut() {
  const router = useRouter();
  return () => signOut().then(() => router.push('/auth/sign-in'));
}

export function SignOutButton() {
  const t = useTranslations('common');
  const signOutAndLeave = useSignOut();
  return (
    <Button
      variant="ghost"
      size="icon-md"
      className="md:size-auto md:px-3"
      onClick={signOutAndLeave}
      aria-label={t('signOut')}
      title={t('signOut')}
    >
      <LogOut className="md:hidden" />
      <span className="hidden md:inline">{t('signOut')}</span>
    </Button>
  );
}
