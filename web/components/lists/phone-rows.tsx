'use client';

import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { BottomSheetContent } from '@/components/ui/bottom-sheet';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Dialog, DialogTitle } from '@/components/ui/dialog';
import { toast } from '@/components/ui/sonner';
import { useCopy } from '@/lib/hooks/use-copy';
import type { ListColumn, ListRow, RowRating } from '@/lib/lists/api';
import { type Entity, identity } from '@/lib/lists/column-model';
import { type ColumnSpec, displayText, hrefFor } from '@/lib/lists/columns';
import { emailView } from '@/lib/lists/lookups';
import { host, isHttp } from '@/lib/lists/view';
import { cn } from '@/lib/utils';
import { CrmHold } from './crm-hold';
import { AskEmailAgain, EmailChip, NoEmail, RATINGS, RowAvatar, RowName } from './row-cells';
import { RowPhoneAction } from './row-phone-action';
import type { RowSelection } from './use-row-selection';

const ROUND_BUTTON =
  'h-12 rounded-xl border font-semibold flex items-center justify-center gap-2 text-sm';

/** The phone list: a card per row; a tap opens the row in a bottom sheet. */
export function PhoneRows({
  listId,
  chatHref,
  columns,
  entity,
  rows,
  specs,
  selection,
  onRate,
  onToggleExcluded,
  onRowChange,
  phoneCredits,
}: {
  listId: string;
  /** The list's chat, where «Chiedi in chat» leads; null without one. */
  chatHref: string | null;
  /** The list's columns, for the required ones a row misses. */
  columns: ListColumn[];
  entity: Entity;
  rows: ListRow[];
  specs: ColumnSpec[];
  selection: RowSelection;
  onRate: (row: ListRow, rating: RowRating) => void;
  onToggleExcluded: (row: ListRow) => void;
  onRowChange: (row: ListRow) => void;
  /** What a found phone number costs (the list's `prices.phone`). */
  phoneCredits: number;
}) {
  const [openId, setOpenId] = useState<string | null>(null);
  // Closing keeps the id, so the sheet still holds its row while it slides out.
  const [shown, setShown] = useState(false);
  // By id, so the sheet shows the row's latest state after a rating.
  const open = rows.find((r) => r.id === openId);
  const show = (id: string) => {
    setOpenId(id);
    setShown(true);
  };

  return (
    <>
      <ul className="flex flex-col gap-2">
        {rows.map((row) => (
          <li key={row.id} className="relative">
            <RowCard
              row={row}
              entity={entity}
              selected={selection.has(row.id)}
              onOpen={() => show(row.id)}
            />
            <RowCheck row={row} entity={entity} selection={selection} />
          </li>
        ))}
      </ul>
      <Dialog open={shown && open !== undefined} onOpenChange={setShown}>
        <BottomSheetContent aria-describedby={undefined} onDismiss={() => setShown(false)}>
          {open && (
            <RowSheet
              listId={listId}
              chatHref={chatHref}
              columns={columns}
              onRowChange={onRowChange}
              row={open}
              entity={entity}
              specs={specs}
              onRate={onRate}
              onToggleExcluded={onToggleExcluded}
              phoneCredits={phoneCredits}
            />
          )}
        </BottomSheetContent>
      </Dialog>
    </>
  );
}

/** Beside the card, not in it: a tap on the box checks the row and never opens the sheet. */
function RowCheck({
  row,
  entity,
  selection,
}: {
  row: ListRow;
  entity: Entity;
  selection: RowSelection;
}) {
  const t = useTranslations('lists');
  return (
    <label
      htmlFor={`select-${row.id}`}
      className="absolute top-0 right-0 flex size-11 cursor-pointer items-center justify-center"
    >
      <Checkbox
        id={`select-${row.id}`}
        checked={selection.has(row.id)}
        onCheckedChange={() => selection.toggle(row.id)}
        aria-label={t('bulk.selectRow', { name: identity(row, entity).name || t('unnamed') })}
      />
    </label>
  );
}

function RowCard({
  row,
  entity,
  selected,
  onOpen,
}: {
  row: ListRow;
  entity: Entity;
  selected: boolean;
  onOpen: () => void;
}) {
  const t = useTranslations('lists');
  const { name, role, company, domain } = identity(row, entity);
  const email = displayText(row.cells.email);
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        'focus-visible:outline-accent-ink flex w-full flex-col gap-1.5 rounded-xl border p-3.5 pr-11 text-left focus-visible:outline-2 focus-visible:outline-offset-2',
        selected ? 'bg-accent-tint' : 'bg-ground',
        row.excluded && 'opacity-50',
      )}
    >
      <span className="flex items-center gap-2.5">
        <span className="bg-accent-tint text-accent-ink flex size-10 shrink-0 items-center justify-center rounded-full text-sm font-bold">
          {row.relevance_score === null ? '—' : Math.round(row.relevance_score)}
        </span>
        <span className="flex min-w-0 grow flex-col">
          <span className="font-bold">
            <RowName name={name} />
          </span>
          <span className="text-muted truncate text-xs-plus">
            {entity === 'person' ? [role, company].filter(Boolean).join(' · ') : domain}
          </span>
        </span>
      </span>
      {row.people_count !== undefined && (
        <span className="text-muted text-xs-plus">
          {t('column.people')}: {row.people_count || '—'}
        </span>
      )}
      {entity === 'person' && (
        <span className="flex items-center gap-2 text-xs-plus">
          <span className="text-muted min-w-0 grow truncate">{email || <NoEmail row={row} />}</span>
          <EmailChip row={row} />
        </span>
      )}
      {row.crm_hold && <CrmHold hold={row.crm_hold} />}
    </button>
  );
}

function RowSheet({
  listId,
  chatHref,
  columns,
  onRowChange,
  row,
  entity,
  specs,
  onRate,
  onToggleExcluded,
  phoneCredits,
}: {
  listId: string;
  chatHref: string | null;
  columns: ListColumn[];
  onRowChange: (row: ListRow) => void;
  row: ListRow;
  entity: Entity;
  specs: ColumnSpec[];
  onRate: (row: ListRow, rating: RowRating) => void;
  onToggleExcluded: (row: ListRow) => void;
  phoneCredits: number;
}) {
  const t = useTranslations('lists');
  const { name, role, company, domain } = identity(row, entity);
  const email = displayText(row.cells.email);
  const linkedin = displayText(row.cells.linkedin_url);
  const research = specs.filter(
    (s) =>
      s.render === 'ai' &&
      displayText(row.cells[s.key]) !== '' &&
      row.cell_meta?.[s.key]?.status !== 'empty',
  );

  const { copy } = useCopy();
  const copyEmail = async () => {
    if (await copy(email)) toast.success(t('emailCopied'));
    else toast.error(t('updateError'));
  };

  return (
    <>
      <div className="flex items-center gap-3">
        <RowAvatar name={name} entity={entity} className="size-12" />
        <div className="flex min-w-0 grow flex-col">
          <DialogTitle className="text-lg font-bold">
            <RowName name={name} />
          </DialogTitle>
          <span className="text-muted text-sm">
            {entity === 'person' ? [role, company].filter(Boolean).join(' · ') : domain}
          </span>
        </div>
        {row.relevance_score !== null && (
          <span className="text-muted text-right text-xs-plus">
            {t('column.fit')}
            <br />
            <strong className="text-ink text-lg">{Math.round(row.relevance_score)}</strong>
          </span>
        )}
      </div>
      {row.crm_hold && <CrmHold hold={row.crm_hold} detail />}
      {entity === 'person' && (
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            disabled={!email}
            onClick={copyEmail}
            className={cn(ROUND_BUTTON, 'disabled:opacity-50')}
          >
            {t('copyEmail')}
          </button>
          {linkedin ? (
            <a href={hrefFor(linkedin)} target="_blank" rel="noreferrer" className={ROUND_BUTTON}>
              {t('openLinkedin')}
            </a>
          ) : (
            <span aria-disabled="true" className={cn(ROUND_BUTTON, 'opacity-50')}>
              {t('openLinkedin')}
            </span>
          )}
        </div>
      )}
      {emailView(row.lookups.email).askAgain && (
        <p className="text-muted flex gap-2 text-sm">
          <NoEmail row={row} />
          <AskEmailAgain row={row} chatHref={chatHref} columns={columns} />
        </p>
      )}
      {entity === 'person' && !row.excluded && (
        <RowPhoneAction
          listId={listId}
          row={row}
          credits={phoneCredits}
          onRowChange={onRowChange}
        />
      )}
      {research.map((s) => {
        const source = row.cell_meta?.[s.key]?.source;
        return (
          <div
            key={s.key}
            className="bg-ground flex flex-col gap-1 rounded-xl border p-3.5 text-sm leading-normal"
          >
            <span className="text-muted text-xs font-semibold">{s.label}</span>
            <span>{displayText(row.cells[s.key])}</span>
            {source && isHttp(source.url) && (
              <a
                href={source.url}
                target="_blank"
                rel="noreferrer"
                className="text-accent-ink text-xs-plus"
              >
                {t('asideSource', {
                  source: source.title || host(source.url),
                })}
              </a>
            )}
          </div>
        );
      })}
      <fieldset className="grid grid-cols-2 gap-2">
        <legend className="sr-only">{t('column.rate')}</legend>
        {RATINGS.map(({ rating, labelKey, on }) => (
          <button
            key={rating}
            type="button"
            aria-pressed={row.rating === rating}
            onClick={() => onRate(row, rating)}
            className={cn(ROUND_BUTTON, 'font-bold', row.rating === rating && on)}
          >
            {t(labelKey)}
          </button>
        ))}
      </fieldset>
      <Button
        variant="outline"
        className="h-11 font-semibold"
        onClick={() => onToggleExcluded(row)}
      >
        {row.excluded ? t('include') : t('exclude')}
      </Button>
    </>
  );
}
