import Link from 'next/link';
import { cn } from '@/lib/utils';

export type TabLink = {
  key: string;
  label: string;
  href: string;
  /** A string so that a capped total reads «200+». */
  count?: string;
};

/** The tab look of `ui/tabs.tsx` for tabs that are links or toggle buttons: `on` is the current one. */
export function tabClass(on: boolean) {
  return cn(
    'text-ui-strong inline-flex h-8 shrink-0 items-baseline gap-1.5 border-b-2 pt-1.75 whitespace-nowrap transition-colors outline-none focus-visible:rounded-sm focus-visible:ring-3 focus-visible:ring-accent/50',
    on ? 'border-ink text-ink' : 'border-transparent text-muted hover:text-ink',
  );
}

/** A tab strip of links: the current tab comes from the page's search param. */
export function TabLinks({
  tabs,
  active,
  label,
}: {
  tabs: readonly TabLink[];
  active: string;
  label: string;
}) {
  return (
    <nav
      aria-label={label}
      className="border-hair flex gap-4.5 overflow-x-auto overflow-y-hidden border-b scrollbar-none"
    >
      {tabs.map(({ key, label: text, href, count }) => (
        <Link
          key={key}
          href={href}
          aria-current={key === active ? 'page' : undefined}
          // h-11 adds 12 px to h-8: the design's 7 px plus 12 keeps the label on the underline.
          className={cn(tabClass(key === active), 'phone:h-11 phone:pt-4.75')}
        >
          {text}
          {count !== undefined && (
            <span className="text-data-sm text-muted font-normal">{count}</span>
          )}
        </Link>
      ))}
    </nav>
  );
}
