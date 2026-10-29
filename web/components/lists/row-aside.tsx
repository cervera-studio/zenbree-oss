'use client';

import { X } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { type ReactNode, useEffect, useRef } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useEscape } from '@/lib/hooks/use-escape';
import type { ListRow, RowRating } from '@/lib/lists/api';
import { type Entity, identity } from '@/lib/lists/column-model';
import { type ColumnSpec, cellValue, displayText, hrefFor } from '@/lib/lists/columns';
import { originGroups } from '@/lib/lists/origin';
import { host, isHttp } from '@/lib/lists/view';
import { CrmHold } from './crm-hold';
import { EmailChip, RateToggles, RowAvatar, RowName } from './row-cells';
import { RowPhoneAction } from './row-phone-action';

const SECTION_TITLE = 'font-mono text-2xs font-medium tracking-label uppercase';

/** Cells the header, the email chip or the phone action already shows. */
const HEADER_KEYS = new Set([
  'full_name',
  'first_name',
  'last_name',
  'title',
  'name',
  'email_status',
  'phone_status',
]);

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline gap-3">
      <dt className="text-muted w-20 shrink-0">{label}</dt>
      <dd className="flex min-w-0 flex-wrap items-center gap-2 break-words">{children}</dd>
    </div>
  );
}

function FieldValue({ spec, row }: { spec: ColumnSpec; row: ListRow }) {
  const t = useTranslations('lists');
  const text = displayText(cellValue(row, spec.key));
  if (spec.render === 'email') {
    return (
      <>
        <a href={`mailto:${text}`} className="underline-offset-2 hover:underline">
          {text}
        </a>
        <EmailChip row={row} />
      </>
    );
  }
  if (spec.render === 'link') {
    return (
      <a
        href={hrefFor(text)}
        target="_blank"
        rel="noreferrer"
        className="text-accent-ink underline-offset-2 hover:underline"
      >
        {spec.key === 'linkedin_url' ? t('asideProfile') : host(text)}
      </a>
    );
  }
  return <>{text}</>;
}

export function RowAside({
  listId,
  entity,
  specs,
  row,
  onClose,
  onToggleExcluded,
  onRowChange,
  onRate,
  phoneCredits,
}: {
  listId: string;
  entity: Entity;
  specs: ColumnSpec[];
  row: ListRow;
  onClose: () => void;
  onToggleExcluded: (row: ListRow) => void;
  onRowChange: (row: ListRow) => void;
  /** The table's Valuta column scrolls out of sight beside this panel, so the rating lives here too. */
  onRate: (row: ListRow, rating: RowRating) => void;
  /** What a found phone number costs (the list's `prices.phone`). */
  phoneCredits: number;
}) {
  const t = useTranslations('lists');
  const ref = useRef<HTMLElement>(null);
  const { name, role, company, domain } = identity(row, entity);
  const subtitle = entity === 'person' ? [role, company].filter(Boolean).join(' · ') : domain;

  useEffect(() => {
    ref.current?.focus();
  }, []);

  useEscape(onClose);

  const fields = specs.filter(
    (s) =>
      s.render !== 'ai' &&
      s.render !== 'score' &&
      !HEADER_KEYS.has(s.key) &&
      displayText(cellValue(row, s.key)) !== '',
  );
  const research = specs.filter((s) => {
    const meta = row.cell_meta?.[s.key];
    return (
      s.render === 'ai' &&
      displayText(row.cells[s.key]) !== '' &&
      (meta?.status === 'filled' || meta?.status === 'manual')
    );
  });
  const origin = originGroups(row, specs);

  return (
    <aside
      ref={ref}
      tabIndex={-1}
      aria-label={t('asideLabel')}
      className="bg-ground w-95 shrink-0 border-l outline-none"
    >
      <div className="sticky top-0 flex max-h-dvh flex-col gap-5 overflow-y-auto p-5">
        <div className="flex items-start gap-3">
          <RowAvatar name={name} entity={entity} className="size-11" />
          <div className="flex min-w-0 grow flex-col gap-0.5">
            <h2 className="text-lede font-bold">
              <RowName name={name} />
            </h2>
            <span className="text-muted text-xs-plus">{subtitle}</span>
          </div>
          <Button variant="ghost" size="icon-sm" aria-label={t('closeDetail')} onClick={onClose}>
            <X />
          </Button>
        </div>
        {row.crm_hold && <CrmHold hold={row.crm_hold} detail />}

        <dl className="text-xs-plus flex flex-col gap-2.5">
          {fields.map((s) => (
            <Field key={s.key} label={s.label}>
              <FieldValue spec={s} row={row} />
            </Field>
          ))}
          {row.people_count !== undefined && (
            <Field label={t('column.people')}>{row.people_count || '—'}</Field>
          )}
          {row.relevance_score !== null && (
            <Field label={t('column.fit')}>
              <strong>{t('asideFit', { score: Math.round(row.relevance_score) })}</strong>
              {row.score_reasons && row.score_reasons.length > 0 && (
                <ul className="text-muted flex w-full flex-col gap-1.5 text-xs">
                  {row.score_reasons.map((r) => (
                    <li key={`${r.category}:${r.match}`} className="flex flex-col">
                      <span className="text-ink">{r.match}</span>
                      <span>
                        {t(`reasonCategory.${r.category}`)}
                        {r.freshness && ` · ${r.freshness}`}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Field>
          )}
        </dl>

        {research.length > 0 && (
          <section className="flex flex-col gap-2.5">
            <h3 className={`${SECTION_TITLE} text-accent-ink`}>{t('asideAi')}</h3>
            {research.map((s) => {
              const source = row.cell_meta?.[s.key]?.source;
              return (
                <div key={s.key} className="bg-ground flex flex-col gap-1 rounded-lg border p-3">
                  <span className="text-muted text-xs">{s.label}</span>
                  <span className="text-xs-plus leading-normal">
                    {displayText(row.cells[s.key])}
                  </span>
                  {source && isHttp(source.url) && (
                    <a
                      href={source.url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-accent-ink text-xs"
                    >
                      {t('asideSource', { source: source.title || host(source.url) })}
                    </a>
                  )}
                </div>
              );
            })}
          </section>
        )}

        {origin.length > 0 && (
          <section className="flex flex-col gap-2">
            <h3 className={`${SECTION_TITLE} text-muted`}>{t('asideOrigin')}</h3>
            <ul className="text-xs-plus flex flex-col gap-1.5">
              {origin.map(([kind, labels]) => (
                <li key={kind} className="flex items-center gap-2">
                  <Badge tone="neutral">{t(`asideOriginKind.${kind}`)}</Badge>
                  {labels.join(', ')}
                </li>
              ))}
            </ul>
          </section>
        )}

        <div className="mt-auto flex flex-col gap-2">
          <div className="flex items-center justify-between gap-3">
            <span className="text-muted text-xs-plus">{t('column.rate')}</span>
            <RateToggles row={row} onRate={onRate} />
          </div>
          {entity === 'person' && !row.excluded && (
            <RowPhoneAction
              listId={listId}
              row={row}
              credits={phoneCredits}
              onRowChange={onRowChange}
            />
          )}
          <Button variant="outline" className="grow" onClick={() => onToggleExcluded(row)}>
            {row.excluded ? t('include') : t('exclude')}
          </Button>
        </div>
      </div>
    </aside>
  );
}
