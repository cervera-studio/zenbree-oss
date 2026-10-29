import { ListTodo, Mail, MessageSquare, UserPlus } from 'lucide-react';
import type { Channel } from '@/lib/campaigns/steps';
import { cn } from '@/lib/utils';

const ICONS = {
  email: Mail,
  linkedin_connect: UserPlus,
  linkedin_message: MessageSquare,
  hubspot_task: ListTodo,
} as const;

/** A step's channel in a 22 px tile: email in the accent tint, the other channels on a raised tile. */
export function StepIcon({ channel, className }: { channel: Channel; className?: string }) {
  const Icon = ICONS[channel];
  return (
    <span
      aria-hidden
      className={cn(
        'flex size-5.5 shrink-0 items-center justify-center rounded-sm',
        channel === 'email'
          ? 'bg-accent-tint text-accent-ink'
          : 'border border-line bg-raised text-soft',
        className,
      )}
    >
      <Icon className="size-3.25" />
    </span>
  );
}
