import { type ReactNode, useId } from 'react';
import { cn } from '@/lib/utils';

/** A framed block with a head at least 44 px high: the title, an optional meta line and an action slot on
 *  the right (a link, a button, a legend). The head wraps when they do not fit, so an action can
 *  take its own line. `flush` drops the body padding for a list of rows. The title
 *  names the section, so each panel is a region a screen reader can jump to. */
export function Panel({
  title,
  meta,
  action,
  flush = false,
  className,
  children,
}: {
  title: string;
  meta?: string;
  action?: ReactNode;
  flush?: boolean;
  className?: string;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <section
      aria-labelledby={id}
      className={cn('border-line bg-ground rounded-lg border', className)}
    >
      <header className="border-line flex min-h-11 flex-wrap items-center gap-x-2.5 gap-y-1 py-1.5 border-b px-3">
        <h2 id={id} className="text-ui-strong">
          {title}
        </h2>
        <span className="text-data-sm text-muted flex-1 truncate font-mono">{meta}</span>
        {action}
      </header>
      <div className={cn('text-soft flex flex-col', !flush && 'px-3 py-4')}>{children}</div>
    </section>
  );
}
