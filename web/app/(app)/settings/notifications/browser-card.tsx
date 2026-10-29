'use client';

import { Bell, Check } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useEffect, useState } from 'react';
import { SettingsCard } from '@/components/settings/settings-card';
import { StatusNotice } from '@/components/settings/status-notice';
import { Button } from '@/components/ui/button';
import { toast } from '@/components/ui/sonner';
import { enablePush, localEndpoint, sendTestPush, turnOffPush } from '@/lib/notifications/push';
import { usePushPermission } from '@/lib/notifications/push-permission';
import type { PushConfig } from '@/lib/notifications/push-schema';

/** Permission plus whether this browser holds a push subscription of the signed-in user. */
export function useBrowserPush({ publicKey, subscribed: initial }: PushConfig) {
  const t = useTranslations('notifications.settings.browser');
  const { permission, reread } = usePushPermission();
  const [endpoint, setEndpoint] = useState<string | null | undefined>(undefined);
  const [subscribed, setSubscribed] = useState(initial);

  useEffect(() => {
    let live = true;
    void localEndpoint().then((e) => {
      if (live) setEndpoint(e);
    });
    return () => {
      live = false;
    };
  }, []);

  const enable = async () => {
    const saved = await enablePush(publicKey);
    reread();
    if (saved) {
      setEndpoint(saved);
      setSubscribed((s) => (s.includes(saved) ? s : [...s, saved]));
      toast.success(t('enabled'));
    } else if (Notification.permission === 'granted') {
      // Granted with no key is the in-page alert working; granted with a key means the subscription
      // failed. A dismissed or blocked prompt is the user's answer, not an error.
      if (publicKey) toast.error(t('enableError'));
      else toast.success(t('enabled'));
    }
  };

  const turnOff = async () => {
    await turnOffPush();
    setEndpoint(null);
    setSubscribed((s) => s.filter((e) => e !== endpoint));
    toast.success(t('turnedOff'));
  };

  const test = async () => {
    if (!endpoint) return;
    if (await sendTestPush(endpoint)) toast.success(t('testSent'));
    else toast.error(t('testError'));
  };

  const active = permission === 'granted' && !!endpoint && subscribed.includes(endpoint);
  const loading = permission === 'granted' && endpoint === undefined;
  return { permission, canEnable: !!publicKey, active, loading, enable, test, turnOff };
}

export function BrowserCard({
  permission,
  canEnable,
  active,
  loading,
  enable,
  test,
  turnOff,
}: ReturnType<typeof useBrowserPush>) {
  const t = useTranslations('notifications.settings.browser');
  if (loading) return null;
  if (permission === 'unsupported' || permission === 'denied') {
    const blocked = permission === 'denied';
    const state = blocked ? 'denied' : 'unsupported';
    return (
      <StatusNotice tone={blocked ? 'warning' : 'neutral'}>
        <span className="text-ink text-small">
          <strong>{t(`${state}.title`)}</strong> {t(`${state}.body`)}
        </span>
      </StatusNotice>
    );
  }
  if (active || (permission === 'granted' && !canEnable)) {
    return (
      <SettingsCard className="bg-raised gap-2.5 p-4">
        <p role="status" className="text-positive text-ui-strong flex items-center gap-2">
          <Check aria-hidden className="size-4" strokeWidth={2.4} />
          {t('on')}
        </p>
        {active && (
          <div className="flex gap-3">
            <Button
              type="button"
              variant="link"
              className="text-ink h-auto p-0 underline"
              onClick={test}
            >
              {t('test')}
            </Button>
            <Button
              type="button"
              variant="link"
              className="text-muted h-auto p-0 underline"
              onClick={turnOff}
            >
              {t('turnOff')}
            </Button>
          </div>
        )}
      </SettingsCard>
    );
  }
  // Without VAPID keys the permission still matters: it is what the in-page alert needs.
  return (
    <SettingsCard className="bg-raised gap-2.5 p-4">
      <div role="status" className="flex flex-col gap-2.5">
        <h3 className="flex items-center gap-2">
          <Bell aria-hidden className="size-4" />
          <span className="text-ui-strong">{t('title')}</span>
        </h3>
        <p className="text-soft text-small">
          <strong className="text-ink">{t(canEnable ? 'ask.title' : 'ask.inPageTitle')}</strong>{' '}
          {t('ask.body')}
        </p>
        <Button type="button" className="self-start phone:h-11" onClick={enable}>
          {t('allow')}
        </Button>
      </div>
    </SettingsCard>
  );
}
