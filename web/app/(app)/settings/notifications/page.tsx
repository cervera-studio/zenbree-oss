import { getTranslations } from 'next-intl/server';
import { SettingsPageHeader } from '@/components/settings/settings-page-header';
import { getToken } from '@/lib/auth-token';
import { httpClient } from '@/lib/http-client';
import { getPushConfig } from '@/lib/notifications/push-config';
import { settingsSchema } from '@/lib/notifications/settings';
import { NotificationSettingsForm } from './settings-form';

export default async function NotificationSettingsPage() {
  const [t, result, push] = await Promise.all([
    getTranslations('notifications.settings'),
    httpClient.get('/v1/notifications/settings', {
      token: await getToken(),
      schema: settingsSchema,
    }),
    getPushConfig(),
  ]);

  return (
    <div className="flex flex-col gap-5">
      <SettingsPageHeader title={t('title')} description={t('description')} />
      {result.success ? (
        <NotificationSettingsForm initial={result.data} push={push} />
      ) : (
        <p role="alert" className="text-muted text-ui">
          {t('loadError')}
        </p>
      )}
    </div>
  );
}
