'use client';

import { Building2, Check, type LucideIcon, User, X } from 'lucide-react';
import Link from 'next/link';
import { useFormatter, useTranslations } from 'next-intl';
import type { ReactNode } from 'react';
import { Badge } from '@/components/ui/badge';
import { Meter } from '@/components/ui/meter';
import { initials } from '@/lib/initials';
import type { ListColumn, ListRow, RowRating } from '@/lib/lists/api';
import { type CompositeId, type Entity, identity } from '@/lib/lists/column-model';
import { displayText } from '@/lib/lists/columns';
import { emailView, phoneView } from '@/lib/lists/lookups';
import { missingRequired } from '@/lib/lists/view';
import { cn } from '@/lib/utils';
import { CrmHold } from './crm-hold';
import { draftHref } from './more-rows-dialog';

export function EmailChip({ row }: { row: ListRow }) {
  const t = useTranslations('lists');
  const { chip } = emailView(row.lookups.email);
  if (!chip) return null;
  return <Badge tone={chip.tone}>{t(chip.labelKey)}</Badge>;
}

/** «In ricerca» while a running turn searches the cell: it pulses, only where motion is fine. */
function Pulsing({ pulse, children }: { pulse: boolean; children: ReactNode }) {
  if (!pulse) return children;
  return (
    <span className="inline-flex items-center gap-2 motion-safe:animate-pulse">
      <span aria-hidden="true" className="bg-hair h-2 w-30 rounded-full" />
      {children}
    </span>
  );
}

/** What a row with no email says: «Non trovata» only once a search answered none. */
export function NoEmail({ row }: { row: ListRow }) {
  const t = useTranslations('lists');
  const { textKey, pulse } = emailView(row.lookups.email);
  return textKey && <Pulsing pulse={pulse}>{t(textKey)}</Pulsing>;
}

/** «Chiedi in chat» under a refunded email: the chat's batch never takes the row again, so the draft names it, and
 * the required columns it misses, which the agent does not see and must fill too or the email is refunded again.
 * None on an excluded row, which no search takes.
 * ponytail: a row the project's exclusion rules match still gets the link, though the search skips it too; the
 * web does not load those rules. Send the rules' verdict in the row payload if that turn confuses users. */
export function AskEmailAgain({
  row,
  chatHref,
  columns,
}: {
  row: ListRow;
  chatHref: string | null;
  columns: ListColumn[];
}) {
  const t = useTranslations('lists');
  const format = useFormatter();
  if (!chatHref || row.excluded || !emailView(row.lookups.email).askAgain) return null;
  const missing = missingRequired(row, columns);
  const draft = missing.length
    ? t('emailRefundedDraftColumns', { row: row.id, columns: format.list(missing) })
    : t('emailRefundedDraft', { row: row.id });
  return (
    <Link href={draftHref(chatHref, draft)} className="text-accent-ink hover:underline">
      {t('askInChat')}
    </Link>
  );
}

/** A row's fit: its score and four segments, in the list page and in the chat's list panel alike. */
export function FitScore({ score }: { score: number | null }) {
  if (score === null) return <span className="text-muted">—</span>;
  return (
    <span className="inline-flex items-center gap-2">
      <span className="text-data text-ink font-mono tabular-nums">{Math.round(score)}</span>
      <Meter kind="fit" value={score / 100} />
    </span>
  );
}

const SECONDARY = 'text-muted text-small';

interface CellProps {
  row: ListRow;
  entity: Entity;
  /** Opens the row's aside. */
  onOpen: () => void;
  /** The list's chat, where «Chiedi in chat» leads; null without one. */
  chatHref: string | null;
  /** The list's columns, for the required ones a row misses. */
  columns: ListColumn[];
}

/** A row's name, or «Senza nome» when the row has none, so its button and titles never go empty. */
export function RowName({ name }: { name: string }) {
  const t = useTranslations('lists');
  return name || <span className="text-muted group-hover:text-accent-ink">{t('unnamed')}</span>;
}

const NO_NAME_ICON: Record<Entity, LucideIcon> = { person: User, company: Building2 };

/** A row's initials, or its entity's icon when the row has no name. `className` sets the size. */
export function RowAvatar({
  name,
  entity,
  className,
}: {
  name: string;
  entity: Entity;
  className: string;
}) {
  const Icon = NO_NAME_ICON[entity];
  return (
    <span
      className={cn(
        'bg-accent-tint text-accent-ink flex shrink-0 items-center justify-center rounded-full font-bold',
        className,
      )}
    >
      {name ? initials(name) : <Icon aria-hidden="true" className="size-1/2" />}
    </span>
  );
}

function NameButton({ id, name, onOpen }: { id: string; name: string; onOpen: () => void }) {
  return (
    <button
      type="button"
      data-row-open={id}
      onClick={onOpen}
      className="group hover:text-accent-ink focus-visible:outline-accent-ink w-fit rounded-xs text-left font-semibold hover:underline focus-visible:outline-2 focus-visible:outline-offset-2"
    >
      <RowName name={name} />
    </button>
  );
}

function CompanyCell({ row, entity, onOpen }: CellProps) {
  const { company, domain } = identity(row, entity);
  return (
    <div className="flex flex-col gap-0.5">
      {entity === 'company' ? (
        <NameButton id={row.id} name={company} onOpen={onOpen} />
      ) : (
        <span>{company}</span>
      )}
      <span className={SECONDARY}>{domain}</span>
    </div>
  );
}

export function EmailCell({
  row,
  chatHref,
  columns,
}: {
  row: ListRow;
  chatHref: string | null;
  columns: ListColumn[];
}) {
  const email = displayText(row.cells.email);
  return (
    <div className="flex flex-col items-start gap-1">
      {email ? (
        <a href={`mailto:${email}`} className="underline-offset-2 hover:underline">
          {email}
        </a>
      ) : (
        <span className="text-muted">
          <NoEmail row={row} />
        </span>
      )}
      <EmailChip row={row} />
      <AskEmailAgain row={row} chatHref={chatHref} columns={columns} />
    </div>
  );
}

export function PhoneCell({ row }: { row: ListRow }) {
  const t = useTranslations('lists');
  const view = phoneView(row.lookups.phone);
  if (!view) return <span>{displayText(row.cells.phone)}</span>;
  return (
    view.textKey && (
      <span className="text-muted">
        <Pulsing pulse={view.pulse}>{t(view.textKey)}</Pulsing>
      </span>
    )
  );
}

function PersonCell({ row, entity, onOpen }: CellProps) {
  const { name, role } = identity(row, entity);
  return (
    <div className="flex flex-col gap-0.5">
      <NameButton id={row.id} name={name} onOpen={onOpen} />
      <span className={SECONDARY}>{role}</span>
      {row.crm_hold && <CrmHold hold={row.crm_hold} />}
    </div>
  );
}

/** One cell renderer per composite column. */
export const COMPOSITE_CELLS: Record<CompositeId, (props: CellProps) => ReactNode> = {
  fit: ({ row }) => <FitScore score={row.relevance_score} />,
  person: PersonCell,
  company: CompanyCell,
  email: ({ row, chatHref, columns }) => (
    <EmailCell row={row} chatHref={chatHref} columns={columns} />
  ),
  phone: ({ row }) => <PhoneCell row={row} />,
};

export const RATINGS = [
  {
    rating: 'good',
    labelKey: 'rateGood',
    Icon: Check,
    on: 'border-positive bg-positive-tint text-positive',
  },
  {
    rating: 'bad',
    labelKey: 'rateBad',
    Icon: X,
    on: 'border-negative bg-negative-tint text-negative',
  },
] as const;

/** «In target» / «Fuori target» as two icon toggles. */
export function RateToggles({
  row,
  onRate,
}: {
  row: ListRow;
  onRate: (row: ListRow, rating: RowRating) => void;
}) {
  const t = useTranslations('lists');
  return (
    <div className="flex gap-1">
      {RATINGS.map(({ rating, labelKey, Icon, on }) => (
        <button
          key={rating}
          type="button"
          aria-pressed={row.rating === rating}
          aria-label={t(labelKey)}
          title={row.rating === rating && row.rating_note ? row.rating_note : undefined}
          onClick={() => onRate(row, rating)}
          className={cn(
            'border-line bg-ground text-muted hover:bg-hair focus-visible:outline-accent-ink flex size-control-sm items-center justify-center rounded-md border focus-visible:outline-2',
            row.rating === rating && on,
          )}
        >
          <Icon className="size-3.5" strokeWidth={2.2} />
        </button>
      ))}
    </div>
  );
}
