import { headers } from 'next/headers';
import { getTranslations } from 'next-intl/server';
import { auth } from '@/lib/auth';
import { AuthShell } from '../auth-shell';
import { safeCallback } from './params';
import { SignInForm } from './sign-in-form';

export default async function SignInPage({
  searchParams,
}: {
  searchParams: Promise<{
    notice?: string;
    callbackUrl?: string | string[];
    // Set by Better Auth when a social sign-in fails (errorCallbackURL).
    error?: string;
  }>;
}) {
  const [{ notice, callbackUrl, error }, t] = await Promise.all([
    searchParams,
    getTranslations('auth'),
  ]);
  // #762: a ban revokes the sessions, so the app sends the banned user here as expired.
  const ban =
    notice === 'expired' ? (await auth.api.signedInBan({ headers: await headers() })).ban : null;
  return (
    <AuthShell layout="split" title={t('signInTitle')} lede={t('signInLede')}>
      <SignInForm
        expired={notice === 'expired'}
        ban={ban}
        callbackUrl={safeCallback(callbackUrl)}
        oauthError={error}
      />
    </AuthShell>
  );
}
