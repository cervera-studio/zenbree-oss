import { MailIcon } from 'lucide-react';
import { getTranslations } from 'next-intl/server';
import { AuthShell } from '../auth-shell';
import { safeCallback } from '../sign-in/params';
import { VerifyActions, VerifyLede } from './verify-email';

export default async function VerifyPage({
  searchParams,
}: {
  searchParams: Promise<{ callbackUrl?: string | string[] }>;
}) {
  const [{ callbackUrl }, t] = await Promise.all([searchParams, getTranslations('auth')]);
  return (
    <AuthShell
      layout="message"
      title={t('verifyTitle')}
      lede={<VerifyLede />}
      icon={
        <span className="flex size-11 items-center justify-center rounded-lg bg-accent-tint text-accent-ink">
          <MailIcon aria-hidden className="size-5.5" strokeWidth={1.75} />
        </span>
      }
    >
      <VerifyActions callbackUrl={safeCallback(callbackUrl)} />
    </AuthShell>
  );
}
