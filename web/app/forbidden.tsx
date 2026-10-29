import Link from 'next/link';
import { getTranslations } from 'next-intl/server';
import { SwitchAccountButton } from '@/components/app/switch-account-button';
import { Logo } from '@/components/logo';
import { Button } from '@/components/ui/button';
import { getSession } from '@/lib/auth-token';
import { logger } from '@/lib/logger';

export default async function Forbidden() {
  const [t, session] = await Promise.all([getTranslations('forbidden'), getSession()]);
  // A code the user can quote to support; the log line ties it to who hit the wall.
  const code = crypto.randomUUID().slice(0, 8).toUpperCase();
  logger.warn('auth.forbidden', { code, userId: session?.user.id });
  return (
    <div className="flex min-h-dvh flex-col px-10 py-7 phone:px-4">
      <Logo className="self-start" />
      <main className="flex flex-1 items-center justify-center">
        <div className="flex max-w-140 flex-col gap-4">
          <p className="text-eyebrow text-muted font-mono uppercase">{t('eyebrow')}</p>
          <h1 className="text-headline phone:text-4xl">{t('title')}</h1>
          <p className="text-body text-muted">
            {t.rich('body', {
              email: session?.user.email ?? '',
              strong: (email) => <strong className="text-ink">{email}</strong>,
            })}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button asChild className="font-semibold">
              <Link href="/">{t('back')}</Link>
            </Button>
            <SwitchAccountButton>{t('switchAccount')}</SwitchAccountButton>
          </div>
          <p className="text-small text-muted">
            {t('supportCode')} <code className="text-data-sm font-mono">{code}</code>
          </p>
        </div>
      </main>
    </div>
  );
}
