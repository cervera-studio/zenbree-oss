'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { AdminPage } from '@/components/admin/admin-page';
import { Button } from '@/components/ui/button';

// A not-found page gets no params: the id comes from the URL.
export default function AdminUserNotFound() {
  const t = useTranslations('admin');
  const id = decodeURIComponent(usePathname().split('/').pop() ?? '');
  return (
    <AdminPage crumbs={[{ label: t('users'), href: '/admin' }, { label: id }]}>
      <section className="border-line-strong flex max-w-2xl flex-col items-start gap-3 rounded-lg border border-dashed p-8">
        <h1 className="text-title">{t('userList.notFound')}</h1>
        <p className="text-muted text-ui">
          {t.rich('userDetail.notFoundBody', {
            id,
            code: (chunks) => <code className="text-ink text-data font-mono">{chunks}</code>,
          })}
        </p>
        <div className="flex flex-wrap gap-2">
          <Button asChild size="sm">
            <Link href="/admin">{t('userDetail.backToUsers')}</Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link href="/admin/costs">{t('userDetail.openCosts')}</Link>
          </Button>
        </div>
      </section>
    </AdminPage>
  );
}
