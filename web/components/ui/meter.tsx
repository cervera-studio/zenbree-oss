import { DOT, type Tone } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

const SEGMENTS = {
  ticks: Array.from({ length: 20 }, (_, n) => `tick-${n}`),
  fit: Array.from({ length: 4 }, (_, n) => `fit-${n}`),
};

/**
 * A share, drawn four ways: a 4 px `bar` (a hairline track and a fill in the accent, or in
 * `tone`), 20 `ticks` that fill the width, a `fit` score of 4 small segments, or a `stacked` pair
 * of 6 px segments (the accent, then lime) sized by their weights.
 */
export function Meter({
  value,
  tone = 'accent',
  kind = 'bar',
}: {
  value: number;
  tone?: Tone;
  kind?: 'bar' | 'ticks' | 'fit' | 'stacked';
}) {
  const share = Math.min(1, Math.max(0, value));
  if (kind === 'stacked') {
    // `value` is the accent segment's share of a bar that is always full: plan credits against
    // purchased ones. A segment of 0 is left out, so no stub shows. The caller skips a total of 0.
    return (
      <span aria-hidden className="flex h-1.5 gap-0.5">
        {share > 0 && (
          <i className="bg-accent block h-full rounded-xs" style={{ flexGrow: share }} />
        )}
        {share < 1 && (
          <i className="bg-lime block h-full rounded-xs" style={{ flexGrow: 1 - share }} />
        )}
      </span>
    );
  }
  if (kind === 'bar') {
    const pct = Math.round(share * 100);
    return (
      <span aria-hidden className="bg-hair block h-1 w-15 shrink-0 overflow-hidden rounded-xs">
        {/* Width from data: a style, not a class, since it is any of 101 values. */}
        <span className={cn('block h-full rounded-xs', DOT[tone])} style={{ width: `${pct}%` }} />
      </span>
    );
  }
  const segments = SEGMENTS[kind];
  const on = Math.round(share * segments.length);
  return (
    <span
      aria-hidden
      className={cn('flex gap-0.5', kind === 'ticks' ? 'w-full' : 'inline-flex items-center')}
    >
      {segments.map((id, i) => (
        <i
          key={id}
          className={cn(
            'block rounded-xs',
            kind === 'ticks' ? 'h-1.5 flex-1' : 'h-2.5 w-0.75',
            i < on ? DOT[tone] : kind === 'ticks' ? 'bg-hair' : 'bg-line',
          )}
        />
      ))}
    </span>
  );
}
