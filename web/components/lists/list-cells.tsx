'use client';

import { CircleAlert, ExternalLink } from 'lucide-react';
import { useTranslations } from 'next-intl';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import type { ListColumn, ListRow } from '@/lib/lists/api';
import { type ColumnSpec, cellValue, displayText, hrefFor } from '@/lib/lists/columns';
import { host, isHttp } from '@/lib/lists/view';
import { EmailCell, FitScore, PhoneCell } from './row-cells';

export function AiCell({
  spec,
  row,
  wrap = false,
}: {
  spec: ColumnSpec;
  row: ListRow;
  wrap?: boolean;
}) {
  const t = useTranslations('lists');
  const meta = row.cell_meta?.[spec.key];
  const text = displayText(cellValue(row, spec.key));
  if (!meta) return null;
  if (meta.status === 'failed') {
    const label = t('aiFailed');
    return (
      <span className="text-negative inline-flex items-center gap-1 text-xs" title={label}>
        <CircleAlert className="size-3 shrink-0" aria-hidden="true" />
        <span aria-hidden="true">{t('aiFailedShort')}</span>
        <span className="sr-only">{label}</span>
      </span>
    );
  }
  if (!text || (meta.status !== 'filled' && meta.status !== 'manual')) {
    const label = t('aiEmpty');
    return (
      <span className="text-muted" title={label}>
        —<span className="sr-only">{label}</span>
      </span>
    );
  }
  const source =
    meta.status === 'filled' && meta.source && isHttp(meta.source.url) ? meta.source : null;
  const label = source && (source.title || source.url);
  if (wrap) {
    return (
      <span className="flex flex-col gap-0.5">
        <span className="leading-snug whitespace-normal">{text}</span>
        {source && (
          <a
            href={source.url}
            target="_blank"
            rel="noreferrer"
            aria-label={t('aiSource', { source: label ?? '' })}
            className="text-accent-ink inline-flex max-w-full items-center gap-1 text-xs hover:underline"
          >
            <ExternalLink className="size-2.75 shrink-0" />
            <span className="truncate">{label}</span>
          </a>
        )}
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1">
      <span className="truncate" title={text}>
        {text}
      </span>
      {source && (
        <a
          href={source.url}
          target="_blank"
          rel="noreferrer"
          title={label ?? undefined}
          aria-label={t('aiSource', { source: label ?? '' })}
          className="text-muted hover:text-ink shrink-0"
        >
          <ExternalLink className="size-3" />
        </a>
      )}
    </span>
  );
}

function ScoreCell({ row }: { row: ListRow }) {
  const t = useTranslations('lists');
  const n = row.relevance_score;
  const badge = <FitScore score={n} />;
  const reasons = row.score_reasons;
  if (n === null || !reasons?.length) return badge;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={t('scoreWhy')}
        className="cursor-pointer rounded hover:ring-1 hover:ring-line focus-visible:outline-2 focus-visible:outline-accent"
      >
        {badge}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-80 space-y-2 p-3 text-sm">
        <p className="font-medium">{t('scoreWhy')}</p>
        {reasons.map((r) => {
          const url = r.key ? row.cell_meta?.[r.key]?.source?.url : undefined;
          return (
            <div key={`${r.category}:${r.match}`} className="space-y-0.5">
              <p className="whitespace-normal">{r.match}</p>
              <div className="text-muted flex flex-wrap items-center gap-x-1.5 text-xs">
                <span>{t(`reasonCategory.${r.category}`)}</span>
                {r.freshness && <span>· {r.freshness}</span>}
                {url && isHttp(url) && (
                  // A menu item, so the link is reachable from the keyboard inside the menu.
                  <DropdownMenuItem
                    asChild
                    className="inline-flex cursor-pointer gap-1 px-1 py-0 text-xs"
                  >
                    <a href={url} target="_blank" rel="noreferrer">
                      {t('reasonSource')} <ExternalLink className="size-3" />
                    </a>
                  </DropdownMenuItem>
                )}
              </div>
            </div>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function Cell({
  spec,
  row,
  chatHref,
  columns,
}: {
  spec: ColumnSpec;
  row: ListRow;
  chatHref: string | null;
  columns: ListColumn[];
}) {
  if (spec.render === 'ai') return <AiCell spec={spec} row={row} />;
  const value = cellValue(row, spec.key);
  if (spec.render === 'score') return <ScoreCell row={row} />;
  if (spec.render === 'phone') return <PhoneCell row={row} />;
  if (spec.render === 'personEmail')
    return <EmailCell row={row} chatHref={chatHref} columns={columns} />;
  const text = displayText(value);
  if (!text) return null;
  if (spec.render === 'link')
    return (
      <a
        href={hrefFor(text)}
        target="_blank"
        rel="noreferrer"
        className="underline-offset-2 hover:underline"
      >
        {host(text)}
      </a>
    );
  if (spec.render === 'email')
    return (
      <a href={`mailto:${text}`} className="underline-offset-2 hover:underline">
        {text}
      </a>
    );
  return <span title={text}>{text}</span>;
}
