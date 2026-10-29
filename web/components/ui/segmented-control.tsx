'use client';

import { type ReactNode, useId } from 'react';

export function SegmentedControl<T extends string>({
  label,
  options,
  value,
  onChange,
  disabled = false,
}: {
  label: string;
  options: readonly { value: T; label: ReactNode }[];
  value: T;
  onChange: (value: T) => void;
  disabled?: boolean;
}) {
  const name = useId();
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="bg-side inline-flex shrink-0 gap-0.5 rounded-md p-0.75 phone:flex phone:w-full phone:flex-wrap"
    >
      {options.map((option) => (
        <label
          key={option.value}
          className="relative text-muted text-xs-plus has-checked:bg-ground has-checked:text-ink has-checked:shadow-xs has-focus-visible:ring-accent has-disabled:opacity-50 flex h-8 cursor-pointer items-center rounded-sm px-3.5 font-semibold phone:h-11 phone:grow phone:justify-center has-focus-visible:ring-2"
        >
          <input
            type="radio"
            name={name}
            value={option.value}
            checked={option.value === value}
            disabled={disabled}
            onChange={() => onChange(option.value)}
            className="sr-only"
          />
          {option.label}
        </label>
      ))}
    </div>
  );
}
