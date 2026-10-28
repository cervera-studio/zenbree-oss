'use client';

import { useRouter } from 'next/navigation';
import { useId, useRef } from 'react';
import { Select } from '@/components/ui/select';

/** The channel filter as the prototype's select: each option is a URL, like the label links.
 * A pick navigates at once, except one made with the keys: arrow keys on a closed select fire
 * `change` on every step (Windows, Firefox), so that one waits for Enter or for focus to leave. */
export function ChannelSelect({
  label,
  value,
  options,
}: {
  label: string;
  value: string;
  options: { value: string; text: string; href: string }[];
}) {
  const router = useRouter();
  const byKey = useRef(false);
  const id = useId();
  const go = (next: string) => {
    const option = options.find((o) => o.value === next);
    if (option && next !== value) router.push(option.href);
  };
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-label text-muted px-2 uppercase">
        {label}
      </label>
      <Select
        id={id}
        aria-label={label}
        size="sm"
        key={value}
        defaultValue={value}
        onPointerDown={() => {
          byKey.current = false;
        }}
        onKeyDown={(e) => {
          byKey.current = true;
          if (e.key === 'Enter') go(e.currentTarget.value);
        }}
        onChange={(e) => {
          if (!byKey.current) go(e.target.value);
        }}
        onBlur={(e) => go(e.target.value)}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.text}
          </option>
        ))}
      </Select>
    </div>
  );
}
