import { ListFilter, Plus } from 'lucide-react';
import Link from 'next/link';
import { getTranslations } from 'next-intl/server';
import { AppPage } from '@/components/app/page';
import { SegmentsIndex } from '@/components/segments/segments-index';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { listSegments } from '@/lib/segments/api';

export default async function SegmentsPage() {
  const [t, result] = await Promise.all([getTranslations('segments'), listSegments()]);
  return (
    <div className="flex min-h-full flex-col">
      <AppPage
        crumbs={[{ label: t('title') }]}
        inProject
        crumbsLabel={t('breadcrumb')}
        topbarActions={
          <Button asChild className="phone:h-11">
            <Link href="/segments/new">
              <Plus aria-hidden />
              {t('new')}
            </Link>
          </Button>
        }
        title={t('title')}
        meta={t('subtitle')}
        className="flex-1"
      >
        {!result.success ? (
          <p role="alert" className="text-negative text-ui">
            {t('loadError')}
          </p>
        ) : result.data.segments.length === 0 ? (
          <EmptyState
            variant="campaigns"
            icon={ListFilter}
            title={t('empty.title')}
            text={t('empty.text')}
          />
        ) : (
          <SegmentsIndex segments={result.data.segments} />
        )}
      </AppPage>
    </div>
  );
}
