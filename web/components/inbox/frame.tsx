import { getTranslations } from 'next-intl/server';
import type { ReactNode } from 'react';
import { AppPage } from '@/components/app/page';
import { cn } from '@/lib/utils';

/**
 * The inbox inside the shell. `flush` is the three-column view: no padding and no title, because
 * the title sits in the filter rail. The shell's scroll box is a block, so `h-full` and `min-h-0`
 * give each column its own scroll instead of the page's. `phone:px-0` is needed beside `p-0`:
 * a variant class is its own group for tailwind-merge.
 */
export async function InboxFrame({
  flush = false,
  busy,
  children,
}: {
  flush?: boolean;
  busy?: boolean;
  children: ReactNode;
}) {
  const t = await getTranslations('inbox');
  return (
    <div className={cn('flex flex-col', flush ? 'h-full' : 'min-h-full')}>
      <AppPage
        crumbs={[{ label: t('title') }]}
        inProject
        crumbsLabel={t('breadcrumb')}
        title={flush ? undefined : t('title')}
        busy={busy}
        className={cn(
          flush
            ? 'min-h-0 flex-1 flex-row gap-0 p-0 phone:flex-col phone:px-0 phone:py-0'
            : 'flex-1',
        )}
      >
        {children}
      </AppPage>
    </div>
  );
}
