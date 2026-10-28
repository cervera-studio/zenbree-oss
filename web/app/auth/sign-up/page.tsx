import { getTranslations } from 'next-intl/server';
import { AuthShell } from '../auth-shell';
import { safeCallback } from '../sign-in/params';
import { SignUpForm } from './sign-up-form';

export default async function SignUpPage({
  searchParams,
}: {
  searchParams: Promise<{ callbackUrl?: string | string[] }>;
}) {
  const [{ callbackUrl }, t] = await Promise.all([searchParams, getTranslations('auth')]);
  return (
    <AuthShell layout="split" title={t('signUpTitle')} lede={t('signUpLede')}>
      <SignUpForm callbackUrl={safeCallback(callbackUrl)} />
    </AuthShell>
  );
}
