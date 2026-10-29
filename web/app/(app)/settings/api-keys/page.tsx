import { getTranslations } from 'next-intl/server';
import { ApiKeyManager } from '@/components/api-keys/api-key-manager';
import { SettingsPageHeader } from '@/components/settings/settings-page-header';
import { getApiKeys } from '@/lib/api-keys';
import { isOrgAdmin } from '@/lib/org';

export default async function ApiKeysPage() {
  const [t, admin] = await Promise.all([getTranslations('apiKeys'), isOrgAdmin()]);
  // The tab is hidden from members; this answers a typed URL (the API says 403 anyway).
  if (!admin) {
    return (
      <div className="flex flex-col gap-5">
        <SettingsPageHeader title={t('sectionTitle')} description={t('adminOnly')} />
      </div>
    );
  }
  const result = await getApiKeys();

  return (
    <div className="flex flex-col gap-5">
      <SettingsPageHeader title={t('sectionTitle')} description={t('description')} />
      <ApiKeyManager
        initialKeys={result.success ? result.data.keys : []}
        loadFailed={!result.success}
      />
    </div>
  );
}
