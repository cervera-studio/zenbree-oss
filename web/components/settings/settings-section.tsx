import { type ReactNode, useId } from 'react';
import { cn } from '@/lib/utils';

export function SettingsSection({
  title,
  description,
  action,
  icon,
  className,
  children,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
  className?: string;
  children?: ReactNode;
}) {
  const titleId = useId();
  // With an icon the section is an entity card (a mailbox, an account) and its description is the
  // line under the name. Without one it opens the body, as in the design's panels.
  const sub = description && (
    <p className={cn('text-small text-muted', !icon && 'mb-3')}>{description}</p>
  );
  return (
    <section
      aria-labelledby={titleId}
      className={cn('border-line bg-ground rounded-lg border', className)}
    >
      <header
        className={cn(
          'border-line flex flex-wrap items-center gap-x-3 gap-y-2',
          icon ? 'px-4 pt-3.5' : 'h-11 border-b px-3',
        )}
      >
        {icon}
        <div className="flex min-w-0 grow flex-col">
          <h3 id={titleId} className="text-ui-strong">
            {title}
          </h3>
          {icon && sub}
        </div>
        {action && <div className="flex items-center gap-2">{action}</div>}
      </header>
      <div className={cn('flex flex-col gap-3', icon ? 'px-4 pt-3 pb-3.5' : 'px-3 py-4')}>
        {!icon && sub}
        {children}
      </div>
    </section>
  );
}

export function SettingRow({
  label,
  labelFor,
  description,
  descriptionId,
  children,
}: {
  label: ReactNode;
  labelFor?: string;
  description?: ReactNode;
  descriptionId?: string;
  children?: ReactNode;
}) {
  return (
    <div className="border-hair flex items-center gap-4 border-b py-3 first:pt-0 last:border-0 last:pb-0 phone:flex-col phone:items-start phone:gap-2.5">
      <div className="flex min-w-0 grow flex-col gap-px">
        <label htmlFor={labelFor} className="text-ui-strong">
          {label}
        </label>
        {description && (
          <span id={descriptionId} className="text-muted text-small">
            {description}
          </span>
        )}
      </div>
      {children}
    </div>
  );
}
