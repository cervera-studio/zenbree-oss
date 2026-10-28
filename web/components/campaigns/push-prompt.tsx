'use client';

import { Bell, X } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { toast } from '@/components/ui/sonner';
import { enablePush } from '@/lib/notifications/push';
import { usePushPermission } from '@/lib/notifications/push-permission';
import { setStored } from '@/lib/storage/storage';
import { useStored } from '@/lib/storage/use-stored';

const DISMISSED = 'zb-push-prompt-dismissed';

/** Shown after a campaign starts, while the browser has not been asked yet: the one moment the reason to
 *  allow notifications is clear (replies come back with Zenbree closed). The button is the user gesture
 *  the permission prompt needs. */
export function PushPrompt({ publicKey }: { publicKey: string }) {
  const t = useTranslations('campaigns.pushPrompt');
  const { permission, reread } = usePushPermission();
  const dismissed = useStored(DISMISSED, 'local', '1') === '1';
  if (permission !== 'default' || dismissed) return null;

  const allow = async () => {
    if (await enablePush(publicKey)) toast.success(t('enabled'));
    // Only a granted permission whose subscription failed is an error: a dismissed prompt is an answer.
    else if (Notification.permission === 'granted') toast.error(t('error'));
    reread();
  };

  return (
    <section
      aria-label={t('title')}
      className="bg-ground flex flex-wrap items-center gap-3.5 rounded-xl border p-4"
    >
      <Bell aria-hidden className="text-accent-ink size-5.5 shrink-0" />
      <p className="min-w-60 flex-1 text-sm leading-normal">
        <strong>{t('title')}</strong> <span className="text-muted">{t('body')}</span>
      </p>
      <Button type="button" className="px-3.5 text-xs-plus font-semibold" onClick={allow}>
        {t('allow')}
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon-md"
        aria-label={t('dismiss')}
        className="size-8"
        onClick={() => setStored(DISMISSED, '1', 'local')}
      >
        <X aria-hidden className="size-4" />
      </Button>
    </section>
  );
}
