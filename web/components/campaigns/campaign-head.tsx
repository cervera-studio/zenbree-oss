import type { ReactNode } from 'react';

/** The head of a campaign page: the name, its state beside it, a meta line and an action on the right. `AppPage`'s head sets the whole title in the `h1`, which would put the state in the heading's name. */
export function CampaignHead({
  title,
  state,
  meta,
  actions,
}: {
  title: ReactNode;
  state: ReactNode;
  meta: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end gap-4">
      <div className="flex min-w-60 flex-1 flex-col gap-0.5">
        <div className="flex flex-wrap items-center gap-2.5">
          {title}
          {state}
        </div>
        <div className="text-small text-muted flex flex-col gap-0.5">{meta}</div>
      </div>
      {actions}
    </div>
  );
}
