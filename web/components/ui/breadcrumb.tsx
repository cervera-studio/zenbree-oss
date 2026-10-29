import Link from 'next/link';
import { cn } from '@/lib/utils';

/** One step of the path in the topbar; the last one has no `href` and is the page itself. */
export type Crumb = { label: string; href?: string };

/** `project`: the active client, before the path. Plain text, and the one that gives way to a short page name. */
export function Breadcrumb({
  crumbs,
  label,
  project,
}: {
  crumbs: readonly Crumb[];
  label: string;
  project?: string;
}) {
  return (
    <nav aria-label={label} className="text-ui flex min-w-0 items-center gap-1.5">
      {project !== undefined && <span className="text-muted min-w-0 truncate">{project}</span>}
      {crumbs.map(({ label: text, href }, i) => (
        <span
          key={`${i}-${text}`}
          className={cn(
            'flex items-center gap-1.5',
            i === 0 ? 'shrink-0' : 'min-w-0',
            // A long chat title between the client and the page would leave the page crumb a sliver.
            i > 0 && i < crumbs.length - 1 && 'phone:max-w-30',
          )}
        >
          {(i > 0 || project !== undefined) && (
            <span aria-hidden className="text-muted">
              /
            </span>
          )}
          {href ? (
            <Link
              href={href}
              className="text-muted hover:text-ink flex min-w-0 items-center phone:min-h-11"
            >
              <span className="truncate">{text}</span>
            </Link>
          ) : (
            <span aria-current="page" className="text-ui-strong truncate">
              {text}
            </span>
          )}
        </span>
      ))}
    </nav>
  );
}
