import { getTranslations } from 'next-intl/server';
import { ExclusionsManager } from '@/components/exclusions/exclusions-manager';
import { SettingsPageHeader } from '@/components/settings/settings-page-header';
import { getExclusions } from '@/lib/exclusions/api';

export default async function ExclusionsPage() {
  const [t, result] = await Promise.all([getTranslations('exclusions'), getExclusions()]);

  return (
    <div className="flex flex-col gap-5">
      <SettingsPageHeader title={t('sectionTitle')} description={t('description')} />
      <ExclusionsManager
        items={result.success ? result.data.exclusions : []}
        loadFailed={!result.success}
      />
    </div>
  );
}
