import { useTranslations } from 'next-intl';
import type { ReactNode } from 'react';
import { AppPage } from '@/components/app/page';
import type { Crumb } from '@/components/ui/breadcrumb';

/** `Backoffice / <section>`: the path of a top level page. */
export const sectionCrumbs = (backoffice: string, section: string): Crumb[] => [
  { label: backoffice, href: '/admin' },
  { label: section },
];

/** Every back office page: the topbar with the path, a page head when given a `title`, and the
 *  frame the content sits in. */
export function AdminPage({
  crumbs,
  title,
  meta,
  actions,
  className,
  busy,
  children,
}: {
  crumbs: readonly Crumb[];
  title?: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  className?: string;
  /** Marks the page as loading for assistive technology. */
  busy?: boolean;
  children: ReactNode;
}) {
  const t = useTranslations('admin.userDetail');
  return (
    <AppPage
      crumbs={crumbs}
      crumbsLabel={t('breadcrumb')}
      title={title}
      meta={meta}
      actions={actions}
      className={className}
      busy={busy}
    >
      {children}
    </AppPage>
  );
}
