import { getTranslations } from 'next-intl/server';
import { AppPage } from '@/components/app/page';
import { ViewsManager } from '@/components/views/views-manager';
import { getViews } from '@/lib/views/api';

export default async function ViewsPage() {
  const [t, result] = await Promise.all([getTranslations('lists.views'), getViews()]);

  return (
    <div className="flex min-h-full flex-col">
      <AppPage
        crumbs={[{ label: t('title') }]}
        inProject
        crumbsLabel={t('breadcrumb')}
        title={t('title')}
        meta={result.success ? t('count', { n: result.data.views.length }) : t('subtitle')}
        className="flex-1"
      >
        {result.success ? (
          <ViewsManager views={result.data.views} />
        ) : (
          <p role="alert" className="text-muted text-ui">
            {t('loadError')}
          </p>
        )}
      </AppPage>
    </div>
  );
}
