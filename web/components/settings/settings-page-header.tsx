import type { ReactNode } from 'react';

/** The design's `sec-head`: a 20 px title, a small line under it and an action on the right. */
export function SettingsPageHeader({
  title,
  description,
  action,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end gap-4">
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <h2 className="text-feature">{title}</h2>
        {description && <p className="text-small text-muted">{description}</p>}
      </div>
      {action}
    </div>
  );
}
