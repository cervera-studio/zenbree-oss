import { Loader2, type LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

/** The page title and lede every step opens with. `compact` is the profile step's smaller title. */
export function StepIntro({
  title,
  lede,
  compact = false,
  className,
}: {
  title: string;
  lede: string;
  compact?: boolean;
  className?: string;
}) {
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <h1
        tabIndex={-1}
        className={cn(
          'font-bold text-ink outline-none',
          compact
            ? 'text-3xl leading-9 tracking-tight'
            : 'text-4xl phone:leading-tight phone:tracking-tight desktop:text-headline',
        )}
      >
        {title}
      </h1>
      <p className={cn('text-soft', compact ? 'text-ui' : 'text-lead')}>{lede}</p>
    </div>
  );
}

/** Mono uppercase label over a group of fields or a card section. */
export function Eyebrow({
  children,
  as: Tag = 'span',
  id,
}: {
  children: ReactNode;
  as?: 'span' | 'h2';
  id?: string;
}) {
  return (
    <Tag id={id} className="m-0 font-mono text-eyebrow text-muted uppercase tabular-nums">
      {children}
    </Tag>
  );
}

/** A button's icon slot: the spinner while its request runs. */
export function PendingIcon({ pending, icon: Icon }: { pending: boolean; icon?: LucideIcon }) {
  if (pending) return <Loader2 className="animate-spin" aria-hidden="true" />;
  return Icon ? <Icon aria-hidden="true" /> : null;
}

export function ErrorNote({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p role="alert" className="text-ui text-negative">
      {message}
    </p>
  );
}
