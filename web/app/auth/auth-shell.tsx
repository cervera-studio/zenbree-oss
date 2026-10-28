import Link from 'next/link';
import { getTranslations } from 'next-intl/server';
import type { ReactNode } from 'react';
import { Logo } from '@/components/logo';
import { cn } from '@/lib/utils';
import { AuthAside } from './auth-aside';
import { LocaleSwitch } from './locale-switch';

// split: the form beside the product pitch (aside from lg up). card: a one-step task on its own.
// message: a card that only tells the user what happens next, centred under an icon.
type AuthLayout = 'split' | 'card' | 'message';

const CARD = 'gap-4 rounded-xl border border-line bg-ground p-6 desktop:p-8';

const PANELS: Record<AuthLayout, string> = {
  split: 'max-w-95 gap-4',
  card: `max-w-115 ${CARD}`,
  message: `max-w-115 items-center text-center ${CARD}`,
};

export async function AuthShell({
  layout,
  title,
  lede,
  icon,
  children,
}: {
  layout: AuthLayout;
  title: string;
  lede?: ReactNode;
  icon?: ReactNode;
  children: ReactNode;
}) {
  const t = await getTranslations('auth');
  return (
    <div className={cn('min-h-svh', layout === 'split' && 'lg:flex')}>
      <div className="flex min-h-svh min-w-0 flex-1 flex-col bg-ground px-4 py-6 desktop:px-10 desktop:py-8">
        <Link href="/" className="w-fit">
          <Logo className="h-6" />
        </Link>
        <main className="flex flex-1 items-center justify-center py-10">
          <div className={cn('flex w-full flex-col', PANELS[layout])}>
            {icon}
            <div className="flex flex-col gap-1">
              <h1 className="text-title text-ink">{title}</h1>
              {lede && <div className="text-ui text-soft">{lede}</div>}
            </div>
            {children}
          </div>
        </main>
        <footer className="text-small flex flex-wrap items-center gap-x-4 gap-y-2 text-muted">
          <span>© {new Date().getFullYear()} Zenbree</span>
          <Link href="/terms" className="hover:text-ink">
            {t('terms')}
          </Link>
          <Link href="/privacy" className="hover:text-ink">
            {t('privacy')}
          </Link>
          <span className="flex-1" />
          <LocaleSwitch />
        </footer>
      </div>
      {layout === 'split' && <AuthAside />}
    </div>
  );
}
