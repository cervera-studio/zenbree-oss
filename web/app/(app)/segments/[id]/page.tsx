import { Users } from 'lucide-react';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { getTranslations } from 'next-intl/server';
import { AppPage } from '@/components/app/page';
import { SegmentControls } from '@/components/segments/segment-controls';
import { SegmentEditor } from '@/components/segments/segment-editor';
import { Button } from '@/components/ui/button';
import { isUuid } from '@/lib/config/uuid';
import { getColumnsOf, getFieldLists, getSegment } from '@/lib/segments/api';
import { listsNamed } from '@/lib/segments/fields';

export default async function SegmentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!isUuid(id)) notFound();
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
  const published = s.published_at !== null;
  const columns = await getColumnsOf(listsNamed(s.rule));
  return (
    <AppPage
      crumbs={[{ label: t('title'), href: '/segments' }, { label: s.name }]}
      crumbsLabel={t('breadcrumb')}
      title={s.name}
      meta={[
        t(`editor.entities.${s.entity}`),
        published ? t('state.published') : t('state.draft'),
        ...(published ? [t('memberCount', { n: s.members })] : []),
      ].join(' · ')}
      topbarActions={
        published && (
          <Button asChild variant="outline" className="phone:h-11">
            <Link href={`/segments/${s.id}/members`}>
              <Users aria-hidden />
              {t('members.open')}
            </Link>
          </Button>
        )
      }
      actions={<SegmentControls id={s.id} name={s.name} published={published} />}
    >
      {lists.success ? (
        <SegmentEditor saved={s} lists={lists.data} columns={columns} />
      ) : (
        <p role="alert" className="text-negative text-ui">
          {t('loadError')}
        </p>
      )}
    </AppPage>
  );
}
