import { notFound } from 'next/navigation';
import { getTranslations } from 'next-intl/server';
import { signupEnabled } from '@/lib/signup';
import { AuthShell } from '../auth-shell';
import { safeCallback } from '../sign-in/params';
import { SignUpForm } from './sign-up-form';

export default async function SignUpPage({
  searchParams,
}: {
  searchParams: Promise<{ callbackUrl?: string | string[] }>;
}) {
  if (!signupEnabled()) notFound();
  const [{ callbackUrl }, t] = await Promise.all([searchParams, getTranslations('auth')]);
  return (
    <AuthShell layout="split" title={t('signUpTitle')} lede={t('signUpLede')}>
      <SignUpForm callbackUrl={safeCallback(callbackUrl)} />
    </AuthShell>
  );
}
