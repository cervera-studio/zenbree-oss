import { Check, ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';

/**
 * The steps of a flow as chips: the ones behind `current` are done (a check), the one at `current`
 * is accent, the rest are quiet numbers. A `current` past the last step marks every step done.
 */
export function Stepper({ steps, current }: { steps: readonly string[]; current: number }) {
  return (
    <ol className="flex items-center gap-2">
      {steps.map((label, i) => {
        const done = i < current;
        return (
          <li
            key={label}
            aria-current={i === current ? 'step' : undefined}
            className="flex items-center gap-2"
          >
            {i > 0 && <ChevronRight aria-hidden className="text-muted size-3 shrink-0" />}
            <span
              className={cn(
                'text-ui-strong inline-flex h-6 items-center gap-1.5 rounded-md px-2',
                done && 'bg-positive-tint text-positive',
                i === current && 'bg-accent-tint text-accent-ink',
                !done && i !== current && 'bg-raised text-muted',
              )}
            >
              {done ? (
                <Check aria-hidden className="size-3" />
              ) : (
                <span className="text-data-sm font-mono">{i + 1}</span>
              )}
              {label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
