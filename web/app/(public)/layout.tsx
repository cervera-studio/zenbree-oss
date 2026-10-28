import Link from 'next/link';
import { getTranslations } from 'next-intl/server';
import type { ReactNode } from 'react';
import { LocaleSwitch } from '@/app/auth/locale-switch';
import { Logo } from '@/components/logo';
import { getPublicApiBaseUrl } from '@/lib/config/env';

// Each page renders `SiteHeader` itself: the landing sets it inside the hero's gradient.
export default async function PublicLayout({ children }: { children: ReactNode }) {
  const t = await getTranslations('public');
  const links = [
    { href: '/terms', label: t('footer.terms') },
    { href: '/privacy', label: t('footer.privacy') },
    { href: `${getPublicApiBaseUrl()}/docs`, label: t('footer.apiDocs') },
    { href: '/auth/sign-in', label: t('signIn') },
  ];
  return (
    <div className="flex min-h-svh flex-col">
      <main className="flex-1">{children}</main>
      <footer className="mx-auto flex w-full max-w-6xl flex-wrap items-center gap-x-6 gap-y-3 px-4 py-8 text-sm text-muted phone:flex-col phone:items-start">
        <Link href="/">
          <Logo className="h-7" />
        </Link>
        <span>© {new Date().getFullYear()} Zenbree</span>
        <nav className="flex flex-1 flex-wrap justify-end gap-x-4 gap-y-2 phone:justify-start">
          {links.map((l) => (
            <Link key={l.href} href={l.href} className="hover:text-ink">
              {l.label}
            </Link>
          ))}
        </nav>
        <LocaleSwitch />
      </footer>
    </div>
  );
}
