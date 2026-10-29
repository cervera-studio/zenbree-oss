import type { ReactNode } from 'react';
import { ProjectBreadcrumb } from '@/components/projects/project-breadcrumb';
import { Breadcrumb, type Crumb } from '@/components/ui/breadcrumb';
import { cn } from '@/lib/utils';

function PageHead({
  title,
  meta,
  actions,
  titleSrOnlyOnPhone,
}: {
  title: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  titleSrOnlyOnPhone?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-end gap-4">
      <div
        className={cn(
          'flex min-w-0 flex-1 flex-col gap-0.5',
          titleSrOnlyOnPhone && 'phone:sr-only',
        )}
      >
        <h1 className="text-title">{title}</h1>
        {meta && <div className="text-small text-muted">{meta}</div>}
      </div>
      {actions}
    </div>
  );
}

/**
 * A page inside a shell: the sticky topbar (the path to the left, `topbarActions` to the right),
 * a page head when given a `title`, and the frame the content sits in. The design's `topbar` and
 * `pageHead` of shell.js.
 */
export function AppPage({
  crumbs,
  crumbsLabel,
  inProject,
  topbarActions,
  title,
  meta,
  actions,
  titleSrOnlyOnPhone,
  className,
  busy,
  children,
}: {
  crumbs: readonly Crumb[];
  /** The accessible name of the path, in the caller's language. */
  crumbsLabel: string;
  /** A top-level page of the active client: the path starts with its name. */
  inProject?: boolean;
  topbarActions?: ReactNode;
  title?: ReactNode;
  meta?: ReactNode;
  /** Beside the title, in the page head. */
  actions?: ReactNode;
  /** The title and meta stay for screen readers on the phone and take no room. `actions` stay visible. */
  titleSrOnlyOnPhone?: boolean;
  className?: string;
  busy?: boolean;
  children: ReactNode;
}) {
  return (
    <>
      <header className="bg-ground sticky top-0 z-10 flex h-topbar shrink-0 items-center gap-2 border-b px-5 phone:px-4">
        <div className="min-w-0 flex-1">
          {inProject ? (
            <ProjectBreadcrumb crumbs={crumbs} label={crumbsLabel} />
          ) : (
            <Breadcrumb crumbs={crumbs} label={crumbsLabel} />
          )}
        </div>
        {topbarActions}
      </header>
      <main
        aria-busy={busy || undefined}
        className={cn('flex flex-col gap-4 px-6 py-5 phone:px-4', className)}
      >
        {title && (
          <PageHead
            title={title}
            meta={meta}
            actions={actions}
            titleSrOnlyOnPhone={titleSrOnlyOnPhone}
          />
        )}
        {children}
      </main>
    </>
  );
}
