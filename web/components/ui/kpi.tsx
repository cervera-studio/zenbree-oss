import { TEXT, type Tone } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

export type Stat = {
  label: string;
  value: string;
  /** Smaller text after the value (a margin %). */
  note?: string;
  /** Colours the note, or the value when there is no note. */
  tone?: Tone;
};

/**
 * The KPI strip on Costi and on a user's usage: one frame, a divider between the stats. A stat
 * never shrinks below its value, so a narrow strip wraps to a new row instead of clipping it.
 */
export function KpiStrip({ stats }: { stats: readonly Stat[] }) {
  return (
    <dl className="border-line bg-line flex flex-wrap gap-px overflow-hidden rounded-lg border">
      {stats.map(({ label, value, note, tone }) => (
        <div key={label} className="bg-ground flex min-w-fit flex-1 flex-col gap-1 p-3">
          <dt className="text-muted text-label truncate uppercase">{label}</dt>
          <dd className="flex items-baseline gap-2 whitespace-nowrap">
            <span className={cn('text-kpi font-mono tabular-nums', tone && !note && TEXT[tone])}>
              {value}
            </span>
            {note && (
              <span className={cn('text-data-sm font-mono tabular-nums', tone && TEXT[tone])}>
                {note}
              </span>
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}
