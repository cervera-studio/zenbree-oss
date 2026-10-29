import { getTranslations } from 'next-intl/server';
import { AppPage } from '@/components/app/page';
import { SegmentEditor } from '@/components/segments/segment-editor';
import { getFieldLists } from '@/lib/segments/api';

export default async function NewSegmentPage() {
  const [t, lists] = await Promise.all([getTranslations('segments'), getFieldLists()]);
  return (
    <AppPage
      crumbs={[{ label: t('title'), href: '/segments' }, { label: t('new') }]}
      crumbsLabel={t('breadcrumb')}
      title={t('new')}
    >
      {lists.success ? (
        <SegmentEditor lists={lists.data} columns={{}} />
      ) : (
        <p role="alert" className="text-negative text-ui">
          {t('loadError')}
        </p>
      )}
    </AppPage>
  );
}
