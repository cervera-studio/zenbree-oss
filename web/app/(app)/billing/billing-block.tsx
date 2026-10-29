import { type ReactNode, useId } from 'react';

export function BillingBlock({
  title,
  action,
  children,
}: {
  title: ReactNode;
  action?: ReactNode;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h3 id={id} className="text-ui-strong grow">
          {title}
        </h3>
        {action}
      </div>
      {children}
    </section>
  );
}
