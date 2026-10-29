/** A share as a 4 px bar over the full width. It is a `progressbar` for the recipe and screen readers; `Meter` is hidden and fixed at 60 px. */
export function ProgressBar({ value, max, label }: { value: number; max: number; label: string }) {
  const pct = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0;
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={value}
      className="bg-hair h-1 w-full overflow-hidden rounded-xs"
    >
      {/* Width from data: a style, not a class, since it is any of 101 values. */}
      <div className="bg-accent h-full rounded-xs" style={{ width: `${pct}%` }} />
    </div>
  );
}
