import type { LucideIcon } from 'lucide-react';
import { type ReactNode, useId } from 'react';
import { cn } from '@/lib/utils';

// The three design empty states (EmptyState in lists, campaigns and inbox.html), and `plain`, the
// dashed box with a bare icon that the admin pages keep.
const VARIANTS = {
  plain: {
    box: 'gap-2 rounded-lg border border-dashed border-line-strong px-6 py-10',
    tile: '',
    icon: 'size-5.5 text-muted',
    text: 'max-w-md',
  },
  lists: {
    box: 'mx-auto w-full max-w-115 gap-3 rounded-xl border border-line bg-raised px-6 py-9',
    tile: 'size-10 rounded-lg border border-line bg-ground text-soft',
    icon: 'size-5',
    text: 'max-w-95',
  },
  campaigns: {
    box: 'flex-1 justify-center gap-4 rounded-xl border border-line bg-raised px-6 py-9',
    tile: 'size-12 rounded-xl bg-accent-tint text-accent-ink',
    icon: 'size-5.5',
    text: 'max-w-130',
  },
  inbox: {
    box: 'flex-1 justify-center gap-2.5 rounded-xl border border-dashed border-line-strong px-6 py-9',
    tile: 'size-10 rounded-lg bg-accent-tint text-accent-ink',
    icon: 'size-5',
    text: 'max-w-115',
  },
} as const;

/**
 * Nothing to show: an icon, a title, why it is empty and what to do next. `note` is a small line
 * under the action. The `lists` variant centres itself at 460 px, `campaigns` and `inbox` fill the
 * height of their parent column.
 */
export function EmptyState({
  variant = 'plain',
  icon: Icon,
  title,
  text,
  action,
  note,
}: {
  variant?: keyof typeof VARIANTS;
  icon: LucideIcon;
  title: string;
  text?: string;
  action?: ReactNode;
  note?: string;
}) {
  const id = useId();
  const v = VARIANTS[variant];
  // An admin page shows one `plain` box per empty table, all with the same title, so naming each
  // would add several regions of one name. The page-sized variants are the screen's content.
  const plain = variant === 'plain';
  const Title = plain ? 'p' : 'h2';
  return (
    <section
      aria-labelledby={plain ? undefined : id}
      className={cn('flex flex-col items-center text-center', v.box)}
    >
      {v.tile ? (
        <span className={cn('flex items-center justify-center', v.tile)}>
          <Icon className={v.icon} aria-hidden />
        </span>
      ) : (
        <Icon className={v.icon} aria-hidden />
      )}
      <Title id={plain ? undefined : id} className={plain ? 'text-ui-strong' : 'text-feature'}>
        {title}
      </Title>
      {text && <p className={cn('text-muted text-ui', v.text)}>{text}</p>}
      {action}
      {note && <p className="text-muted text-small">{note}</p>}
    </section>
  );
}
