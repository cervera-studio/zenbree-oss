'use client';

import { useTranslations } from 'next-intl';
import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useErrorText } from '@/i18n/error-text';

const LOAD_TIMEOUT_MS = 10_000;

const range = (n: number) => Array.from({ length: n }, (_, i) => i);

function Bar({ className = 'w-full' }: { className?: string }) {
  return <div className={`bg-hair h-4 animate-pulse rounded ${className}`} />;
}

/** Placeholder shaped like the table that replaces it. */
export function TableSkeleton({ columns, rows = 6 }: { columns: number; rows?: number }) {
  const t = useTranslations('lists');
  return (
    <div aria-busy="true">
      <span className="sr-only">{t('loading')}</span>
      <Table>
        <TableHeader>
          <TableRow>
            {range(columns).map((c) => (
              <TableHead key={c}>
                <Bar className="w-20" />
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {range(rows).map((r) => (
            <TableRow key={r}>
              {range(columns).map((c) => (
                <TableCell key={c}>
                  <Bar />
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

/** «Impossibile caricare le liste.» with a retry; `detail` is the error code, if any. */
/** `code`: the API's error code, when there was an answer at all (not a timeout). */
export function LoadFailed({ onRetry, code }: { onRetry: () => void; code?: string | null }) {
  const t = useTranslations('lists');
  const errorText = useErrorText();
  return (
    <div role="alert" className="flex flex-wrap items-center gap-3 p-4 text-sm">
      <p className="text-muted">{errorText({ code }, t('error'))}</p>
      <Button size="sm" variant="outline" onClick={onRetry}>
        {t('retry')}
      </Button>
    </div>
  );
}

/**
 * True once `loading` has lasted 10 s; `attempt` (a retry) restarts the clock. Callers bump
 * `attempt` whenever loading starts again, so the timeout belongs to one attempt.
 */
export function useLoadTimeout(loading: boolean, attempt: number): boolean {
  const [timedOutAttempt, setTimedOutAttempt] = useState<number | null>(null);
  useEffect(() => {
    if (!loading) return;
    const timer = setTimeout(() => setTimedOutAttempt(attempt), LOAD_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [loading, attempt]);
  return loading && timedOutAttempt === attempt;
}
