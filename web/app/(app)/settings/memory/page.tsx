import { getTranslations } from 'next-intl/server';
import { MemoryEditor } from '@/components/memory/memory-editor';
import { SettingsPageHeader } from '@/components/settings/settings-page-header';
import { getMemory } from '@/lib/memory';

export default async function MemoryPage() {
  const [t, result] = await Promise.all([getTranslations('memory'), getMemory()]);
  const header = <SettingsPageHeader title={t('sectionTitle')} description={t('description')} />;

  return result.success ? (
    <MemoryEditor initial={result.data} header={header} />
  ) : (
    <div className="flex flex-col gap-4">
      {header}
      <p role="alert" className="text-muted text-ui">
        {t('loadError')}
      </p>
    </div>
  );
}
