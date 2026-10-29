'use client';

import { MessageSquare, Table2 } from 'lucide-react';
import Link from 'next/link';
import { useFormatter, useTranslations } from 'next-intl';
import { type ReactNode, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import type { List, ListColumn, ListRow } from '@/lib/lists/api';
import { fillErrorKey } from '@/lib/lists/columns';
import { createdLabel, LIST_STATUS, type ListKind, listKind } from '@/lib/lists/view';
import { cn } from '@/lib/utils';

export function ListStatusPill({ status }: { status: List['status'] }) {
  const t = useTranslations('lists');
  if (!status) return null;
  const { labelKey, tone } = LIST_STATUS[status];
  return (
    <Badge tone={tone} dot>
      {t(labelKey)}
    </Badge>
  );
}

export function KindPill({ list }: { list: Pick<List, 'mode' | 'entity'> }) {
  const t = useTranslations('lists');
  return <Badge tone="neutral">{t(`kind.${listKind(list)}`)}</Badge>;
}

/** «47 di 50», the first figure bold. `plain` is one run of text, as a table cell sets it. */
export function DeliveredOf({
  done,
  total,
  plain = false,
}: {
  done: number;
  total: number;
  plain?: boolean;
}) {
  const t = useTranslations('lists');
  return t.rich('deliveredOf', {
    done,
    total,
    b: (chunks) => (plain ? chunks : <strong>{chunks}</strong>),
    m: (chunks) => (plain ? chunks : <span className="text-muted font-normal">{chunks}</span>),
  });
}

/** «Oggi, 11:42», «Ieri» or «29 set». */
export function useCreatedText(): (iso: string) => string {
  const t = useTranslations('lists');
  const format = useFormatter();
  const [now] = useState(() => new Date());
  return (iso) => {
    const label = createdLabel(iso, now);
    if (label.kind === 'yesterday') return t('createdYesterday');
    if (label.kind === 'today') {
      return t('createdToday', { time: format.dateTime(label.date, { timeStyle: 'short' }) });
    }
    return format.dateTime(label.date, { day: 'numeric', month: 'short' });
  };
}

/** The API's `empty` means no delivered row, so a list whose rows all miss a contact is `empty` too, and so
 *  is a CSV import that added none, where no search ran. */
export const settledEmpty = (list: Pick<List, 'status' | 'mode'>, rows: readonly ListRow[]) =>
  list.status === 'empty' && list.mode !== 'enrich' && rows.length === 0;

export function NoRows({
  empty,
  chatHref,
  className,
}: {
  empty: boolean;
  chatHref?: string | null;
  className?: string;
}) {
  const t = useTranslations('lists');
  if (!empty) return <p className={cn('text-muted text-sm', className)}>{t('noRows')}</p>;
  return (
    <div className={cn('flex max-w-xl flex-col items-start gap-3', className)}>
      <p className="text-muted text-sm">{t('emptyListBody')}</p>
      {chatHref && (
        <Button asChild variant="outline" size="sm">
          <Link href={chatHref}>
            <MessageSquare /> {t('emptyListRetry')}
          </Link>
        </Button>
      )}
    </div>
  );
}

/** One line per AI column whose last fill run failed, with why; `onRetry` adds a «Riprova» button. */
export function FillFailures({
  columns,
  onRetry,
}: {
  columns: ListColumn[];
  onRetry?: (key: string) => void;
}) {
  const t = useTranslations('lists');
  return columns
    .filter((c) => c.kind === 'ai' && c.config?.fill?.status === 'failed')
    .map((c) => (
      <p key={c.key} role="status" className="text-negative flex items-center gap-2 text-xs">
        <span>
          {c.label}: {t(fillErrorKey(c.config?.fill?.error))}
        </span>
        {onRetry && (
          <Button variant="outline" size="xs" onClick={() => onRetry(c.key)}>
            {t('retry')}
          </Button>
        )}
      </p>
    ));
}

const CHIP_BASE =
  'text-ui-strong inline-flex h-control-sm items-center gap-1.5 rounded-md px-2.25 whitespace-nowrap';
const CHIP_TONES = {
  current: 'bg-accent-tint text-accent-ink',
  plain: 'border border-line bg-ground text-ink',
  dashed: 'text-muted border border-dashed',
} as const;

export function chipClass(tone: keyof typeof CHIP_TONES, className?: string) {
  return cn(CHIP_BASE, CHIP_TONES[tone], className);
}

export function ChipFace({
  kind,
  count,
  quiet = false,
}: {
  kind: ListKind;
  count: ReactNode;
  quiet?: boolean;
}) {
  const t = useTranslations('lists');
  return (
    <>
      <Table2 className="size-3.5" aria-hidden="true" />
      {t(`kind.${kind}`)} <span className={cn('font-normal', quiet && 'text-muted')}>{count}</span>
    </>
  );
}
