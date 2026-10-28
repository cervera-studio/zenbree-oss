import { getTranslations } from 'next-intl/server';
import { AuthShell } from '../auth-shell';
import { safeCallback } from '../sign-in/params';
import { ResetForm } from './reset-form';

export default async function ResetPasswordPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string; new?: string; callbackUrl?: string | string[] }>;
}) {
  const [{ token, new: first, callbackUrl }, t] = await Promise.all([
    searchParams,
    getTranslations('auth'),
  ]);
  // `new`: the first verification of a new account sent the person here to choose a password (#979).
  const isNew = Boolean(token && first);
  return (
    <AuthShell
      layout="card"
      title={t(isNew ? 'setPasswordTitle' : 'resetTitle')}
      lede={t(isNew ? 'setPasswordLede' : token ? 'resetNewLede' : 'resetLede')}
    >
      <ResetForm token={token} isNew={isNew} callbackUrl={safeCallback(callbackUrl)} />
    </AuthShell>
  );
}
