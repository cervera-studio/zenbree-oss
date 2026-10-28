import { getTranslations } from 'next-intl/server';
import { Suspense } from 'react';
import { getMe } from '@/lib/api/me';
import type { Viewer } from './account-menu';
import { CreditsCard, CreditsCardFallback, CreditsCount, CreditsPill } from './credits';
import { MobileNav } from './mobile-nav';
import { SidebarNav } from './sidebar';

/** The app shell's navigation: a 232 px rail from 760 px up, a top bar and a drawer below. */
export async function AppSidebar({ viewer }: { viewer: Viewer }) {
  const t = await getTranslations('nav');
  // One request for both credit counts (rail and phone top bar); each streams in on its own.
  const me = getMe();
  const credits = (
    <Suspense fallback={<CreditsCardFallback orgAdmin={viewer.isOrgAdmin} />}>
      <CreditsCard me={me} orgAdmin={viewer.isOrgAdmin} />
    </Suspense>
  );

  return (
    <>
      <nav
        aria-label={t('primary')}
        className="bg-side flex h-full w-sidebar shrink-0 flex-col gap-1 border-r px-2.5 py-3 phone:hidden"
      >
        <SidebarNav viewer={viewer} credits={credits} />
      </nav>
      <MobileNav
        credits={
          <Suspense fallback={<span className="bg-side h-8 w-24 rounded-full" />}>
            <CreditsPill me={me} orgAdmin={viewer.isOrgAdmin} />
          </Suspense>
        }
      >
        <SidebarNav
          viewer={viewer}
          credits={
            <Suspense fallback={null}>
              <CreditsCount me={me} />
            </Suspense>
          }
          phone
        />
      </MobileNav>
    </>
  );
}
