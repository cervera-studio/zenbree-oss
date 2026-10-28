import { CircleAlert, CircleCheck, Info } from 'lucide-react';
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

const TONES = {
  positive: { box: 'bg-positive-tint text-positive', icon: CircleCheck },
  negative: { box: 'bg-negative-tint text-negative', icon: CircleAlert },
  warning: { box: 'bg-warning-tint text-warning', icon: Info },
  neutral: { box: 'border-line bg-raised text-soft border', icon: Info },
} as const;

export function StatusNotice({
  tone,
  children,
}: {
  tone: keyof typeof TONES;
  children: ReactNode;
}) {
  const { box, icon: Icon } = TONES[tone];
  return (
    <div role="status" className={cn('text-ui flex items-center gap-2.5 rounded-md p-3', box)}>
      <Icon aria-hidden="true" className="size-4 shrink-0" />
      {children}
    </div>
  );
}
