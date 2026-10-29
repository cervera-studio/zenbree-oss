'use client';

import { WifiOff } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';

export function ConnectionBanner() {
  const t = useTranslations('chat.connection');
  return (
    <div
      role="alert"
      className="bg-warning-tint border-line flex min-h-11.5 shrink-0 items-center gap-2.5 border-b px-4 py-1.5 desktop:px-5"
    >
      <WifiOff className="text-warning size-4 shrink-0" aria-hidden="true" />
      <span className="text-ui text-ink grow">{t('banner')}</span>
      <Button size="sm" className="phone:h-11" onClick={() => window.location.reload()}>
        {t('reload')}
      </Button>
    </div>
  );
}
