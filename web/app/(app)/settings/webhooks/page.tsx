import { getTranslations } from 'next-intl/server';
import { SettingsPageHeader } from '@/components/settings/settings-page-header';
import { WebhookManager } from '@/components/webhooks/webhook-manager';
import { isOrgAdmin } from '@/lib/org';
import { getWebhooks } from '@/lib/webhooks/api';

export default async function WebhooksPage() {
  const [t, admin] = await Promise.all([getTranslations('webhooks'), isOrgAdmin()]);
  // The tab is hidden from members; this answers a typed URL (the API says 403 anyway).
  if (!admin) {
    return (
      <div className="flex flex-col gap-5">
        <SettingsPageHeader title={t('sectionTitle')} description={t('adminOnly')} />
      </div>
    );
  }
  const result = await getWebhooks();

  return (
    <div className="flex flex-col gap-5">
      <SettingsPageHeader title={t('sectionTitle')} description={t('description')} />
      <WebhookManager
        initialWebhooks={result.success ? result.data.webhooks : []}
        events={result.success ? result.data.events : []}
        loadFailed={!result.success}
      />
    </div>
  );
}
