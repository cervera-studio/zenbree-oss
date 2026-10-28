import Link from 'next/link';
import { notFound } from 'next/navigation';
import { getTranslations } from 'next-intl/server';
import { AppPage } from '@/components/app/page';
import { MembersList } from '@/components/segments/members-list';
import { Button } from '@/components/ui/button';
import { isUuid } from '@/lib/config/uuid';
import { getColumnsOf, getFieldLists, getMembers, getSegment } from '@/lib/segments/api';
import { listsNamed } from '@/lib/segments/fields';

const PAGE = 50;

export default async function SegmentMembersPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ after?: string | string[] }>;
}) {
  const [{ id }, query] = await Promise.all([params, searchParams]);
  if (!isUuid(id)) notFound();
  const after = typeof query.after === 'string' && isUuid(query.after) ? query.after : null;
  const [t, segment, lists] = await Promise.all([
    getTranslations('segments'),
    getSegment(id),
    getFieldLists(),
  ]);
  if (!segment.success) {
    if (segment.error.status === 404) notFound();
    return (
      <p role="alert" className="text-negative p-6 text-ui">
        {t('loadError')}
      </p>
    );
  }
  const s = segment.data;
  const crumbs = [
    { label: t('title'), href: '/segments' },
    { label: s.name, href: `/segments/${s.id}` },
    { label: t('members.title') },
  ];
  if (s.published_at === null) {
    return (
      <AppPage crumbs={crumbs} crumbsLabel={t('breadcrumb')} title={t('members.title')}>
        <p className="text-ink text-ui">{t('members.draft')}</p>
      </AppPage>
    );
  }
  const page = await getMembers(id, after, PAGE);
  const rowLists = page.success
    ? page.data.members.flatMap((m) => (m.row ? [m.row.list_id] : []))
    : [];
  const columns = await getColumnsOf([...new Set([...listsNamed(s.rule), ...rowLists])]);
  const next = page.success ? page.data.next_cursor : null;
  return (
    <AppPage
      crumbs={crumbs}
      crumbsLabel={t('breadcrumb')}
      title={t('members.title')}
      meta={t('memberCount', { n: s.members })}
    >
      {!page.success || !lists.success ? (
        <p role="alert" className="text-negative text-ui">
          {t('loadError')}
        </p>
      ) : (
        <div className="flex flex-col gap-4">
          <MembersList
            segmentId={s.id}
            members={page.data.members}
            catalog={{
              entity: s.entity,
              lists: lists.data.filter((l) => l.entity === s.entity),
              columns,
            }}
          />
          <nav aria-label={t('members.paging')} className="flex flex-wrap gap-2">
            {after && (
              <Button asChild variant="outline" className="phone:h-11">
                <Link href={`/segments/${s.id}/members`}>{t('members.first')}</Link>
              </Button>
            )}
            {next && (
              <Button asChild variant="outline" className="phone:h-11">
                <Link href={`/segments/${s.id}/members?after=${encodeURIComponent(next)}`}>
                  {t('members.next')}
                </Link>
              </Button>
            )}
          </nav>
        </div>
      )}
    </AppPage>
  );
}
