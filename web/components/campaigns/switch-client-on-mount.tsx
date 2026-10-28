'use client';

import { useTranslations } from 'next-intl';
import { useEffect } from 'react';
import { toast } from '@/components/ui/sonner';
import { setCurrentProjectId } from '@/lib/projects';

export function SwitchClientOnMount({ client }: { client: string }) {
  const t = useTranslations('projects');
  useEffect(() => {
    setCurrentProjectId(client).catch(() => toast.error(t('switchError')));
  }, [client, t]);
  return null;
}
