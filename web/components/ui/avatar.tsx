'use client';

import { useState } from 'react';
import { TINT, type Tone } from '@/components/ui/badge';
import { initials } from '@/lib/initials';
import { hasRole } from '@/lib/roles';
import { cn } from '@/lib/utils';

type Who = { name?: string | null; email: string; role?: string | null };

const SIZE = {
  sm: 'size-5.5 rounded-sm text-3xs',
  md: 'size-6.5 rounded-sm text-3xs',
  lg: 'size-10 rounded-md text-sm',
} as const;

/** The user's photo, or their initials on a tone's tint when there is none or it does not load (a
 *  provider photo that expired, a removed upload). Without a `tone`, admins take the warning tint so
 *  staff stand out in a list, and the rest take the accent. */
export function Avatar({
  user,
  size = 'sm',
  tone,
  image,
}: {
  user: Who;
  size?: keyof typeof SIZE;
  tone?: Tone;
  image?: string | null;
}) {
  const [failed, setFailed] = useState<string | null>(null);
  if (image && failed !== image) {
    return (
      <img
        src={image}
        alt=""
        aria-hidden
        onError={() => setFailed(image)}
        className={cn('shrink-0 object-cover', SIZE[size])}
      />
    );
  }
  return (
    <span
      aria-hidden
      className={cn(
        'flex shrink-0 items-center justify-center font-bold',
        SIZE[size],
        TINT[tone ?? (hasRole(user.role, 'admin') ? 'warning' : 'accent')],
      )}
    >
      {initials(user.name || user.email)}
    </span>
  );
}
