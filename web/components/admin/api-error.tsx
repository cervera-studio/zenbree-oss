import { Inbox, TriangleAlert } from 'lucide-react';
import Link from 'next/link';
import { getTranslations } from 'next-intl/server';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import type { AppErrorJSON } from '@/lib/types';

/** The back office shows the technical detail: who reads it can act on it. */
export async function ApiError({
  error,
  request,
  retryHref,
}: {
  error: AppErrorJSON;
  /** e.g. `GET /v1/admin/costs?from=…`: what failed, next to the status and code. */
  request?: string;
  retryHref?: string;
}) {
  const t = await getTranslations('admin.common');
  return (
    <div
      role="alert"
      className="border-negative bg-negative-tint text-negative flex items-start gap-3 rounded-lg border px-4 py-3.5"
    >
      <TriangleAlert className="mt-px size-4.5 shrink-0" aria-hidden />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <p className="text-ui-strong">{t('apiError')}</p>
        <p className="text-ui">{t('apiErrorBody')}</p>
        <p className="text-data-sm font-mono break-all">
          {request && `${request} → `}
          {error.status} {error.code} · {error.message}
        </p>
      </div>
      {retryHref && (
        <Button asChild size="sm">
          <Link href={retryHref}>{t('retry')}</Link>
        </Button>
      )}
    </div>
  );
}

export async function Empty() {
  const t = await getTranslations('admin.common');
  return <EmptyState icon={Inbox} title={t('empty')} />;
}
