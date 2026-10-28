import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

const TONES = {
  plain: 'border-line',
  danger: 'border-negative',
  dashed: 'border-line border-dashed',
} as const;

/** The design's `card`: a framed block with no head, for a lone fact or an entity (HubSpot, the danger zone). */
export function SettingsCard({
  tone = 'plain',
  className,
  children,
}: {
  tone?: keyof typeof TONES;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        'bg-ground flex flex-col gap-3 rounded-lg border px-3 py-4',
        TONES[tone],
        className,
      )}
    >
      {children}
    </div>
  );
}

/** The design's `item`: a bordered row inside a panel, such as a linked account or a mailbox. */
export function SettingsItem({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div
      className={cn(
        'border-line bg-ground flex flex-wrap items-center gap-3 rounded-md border px-3 py-2.5',
        className,
      )}
    >
      {children}
    </div>
  );
}

/** The design's `tile`: a 32 px square for the icon of an account or an integration. */
export function IconTile({ children }: { children: ReactNode }) {
  return (
    <span
      aria-hidden
      className="border-line bg-raised text-soft flex size-8 shrink-0 items-center justify-center rounded-md border"
    >
      {children}
    </span>
  );
}
