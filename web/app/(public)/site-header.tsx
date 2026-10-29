import Link from 'next/link';
import { connection } from 'next/server';
import { getTranslations } from 'next-intl/server';
import { Logo } from '@/components/logo';
import { Button } from '@/components/ui/button';
import { signupEnabled } from '@/lib/signup';

// No section links: the sections live on the marketing site, whose anchors differ per language.
// The logo's `/` takes a signed-out visitor there.
export async function SiteHeader() {
  // The sign-up flag is read from the runtime env, so the terms and privacy pages must not be prerendered.
  await connection();
  const t = await getTranslations('public');
  return (
    <header className="flex items-center justify-between gap-4 px-7 py-5 phone:px-4">
      <Link href="/">
        <Logo />
      </Link>
      <div className="flex items-center gap-2">
        <Button asChild variant="ghost" className="rounded-full phone:hidden">
          <Link href="/auth/sign-in">{t('signIn')}</Link>
        </Button>
        {signupEnabled() && (
          <Button asChild className="rounded-full bg-ink text-ground hover:bg-ink/90">
            <Link href="/auth/sign-up">{t('tryFree')}</Link>
          </Button>
        )}
      </div>
    </header>
  );
}
