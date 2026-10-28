import { getTranslations } from 'next-intl/server';
import { AppPage } from '@/components/app/page';
import { AutomationEditor } from '@/components/automations/editor/automation-editor';
import { getChoices } from '@/lib/automations/editor-api';

export default async function NewAutomationPage() {
  const [t, te, choices] = await Promise.all([
    getTranslations('automations'),
    getTranslations('automationEditor'),
    getChoices(),
  ]);

  return (
    <AppPage
      crumbs={[{ label: t('title'), href: '/automations' }, { label: te('new') }]}
      crumbsLabel={t('breadcrumb')}
      title={te('new')}
    >
      {choices.success ? (
        <AutomationEditor choices={choices.data} viewerIsCreator />
      ) : (
        <p role="alert" className="text-negative text-ui">
          {te('loadError')}
        </p>
      )}
    </AppPage>
  );
}
