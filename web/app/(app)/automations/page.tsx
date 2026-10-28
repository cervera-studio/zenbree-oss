import { Plus, Workflow } from 'lucide-react';
import Link from 'next/link';
import { getTranslations } from 'next-intl/server';
import { AppPage } from '@/components/app/page';
import { AutomationsIndex } from '@/components/automations/automations-index';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { getAutomations } from '@/lib/automations/api';

export default async function AutomationsPage() {
  const [t, te, result] = await Promise.all([
    getTranslations('automations'),
    getTranslations('automationEditor'),
    getAutomations(),
  ]);

  return (
    <div className="flex min-h-full flex-col">
      <AppPage
        crumbs={[{ label: t('title') }]}
        inProject
        crumbsLabel={t('breadcrumb')}
        topbarActions={
          <Button asChild className="phone:h-11">
            <Link href="/automations/new">
              <Plus aria-hidden />
              <span className="phone:sr-only">{te('new')}</span>
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
        ) : result.data.automations.length === 0 ? (
          <EmptyState
            variant="campaigns"
            icon={Workflow}
            title={t('empty.title')}
            text={t('empty.text')}
          />
        ) : (
          <AutomationsIndex automations={result.data.automations} />
        )}
      </AppPage>
    </div>
  );
}
