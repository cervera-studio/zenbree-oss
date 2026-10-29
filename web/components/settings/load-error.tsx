'use client';

import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { useTransition } from 'react';
import { Button } from '@/components/ui/button';

export function LoadError({ message }: { message: string }) {
  const t = useTranslations('common');
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  return (
    <div role="alert" className="flex flex-wrap items-center gap-3">
      <p className="text-small text-muted">{message}</p>
      <Button
        size="sm"
        variant="outline"
        disabled={pending}
        onClick={() => startTransition(() => router.refresh())}
      >
        {t('retry')}
      </Button>
    </div>
  );
}
