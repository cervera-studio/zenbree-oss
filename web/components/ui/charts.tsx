'use client';

import { type NumberFormatOptions, useFormatter } from 'next-intl';
import {
  CartesianGrid,
  LabelList,
  Line,
  LineChart as RechartsLineChart,
  XAxis,
  YAxis,
} from 'recharts';
import { cn } from '@/lib/utils';
import { type ChartConfig, ChartContainer, ChartTooltip } from './chart';

type Row = Record<string, string | number>;

/** Truncate a long axis label so ticks stay legible. */
function truncateLabel(value: string, max: number): string {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

/**
 * One named line on a `MultiLineChart`.
 *
 * `color` is a CSS colour and in practice always a `--chart-N` token: the ramp
 * has a light AND a dark value per token, so a token follows the theme while a
 * literal does not. A caller passing a hex here is the bug this comment exists
 * to catch in review.
 */
export interface ChartSeries {
  /** Key of this series' value on each row of `data`. */
  dataKey: string;
  /** Display name, already translated by the caller. */
  label: string;
  color: string;
  dashed?: boolean;
}

export interface ChartTooltipRow {
  dataKey: string;
  label: string;
}

const END_LABEL_OFFSET = 8;
const END_LABEL_LINE_HEIGHT = 14;

/**
 * Vertical shift of each end label, by `dataKey`. Lines that end on the same
 * value («Fornitori» and «LLM» both 0 on a quiet day) would print their labels
 * on top of each other, so each group is stacked and centred on its point.
 * ponytail: equal values only; values a few pixels apart still touch.
 */
export function endLabelShifts(series: readonly ChartSeries[], last: Row | undefined) {
  const groups = new Map<string | number | undefined, string[]>();
  for (const { dataKey } of series) {
    const value = last?.[dataKey];
    groups.set(value, [...(groups.get(value) ?? []), dataKey]);
  }
  const shifts: Record<string, number> = {};
  for (const keys of groups.values())
    keys.forEach((key, i) => {
      shifts[key] = (i - (keys.length - 1) / 2) * END_LABEL_LINE_HEIGHT;
    });
  return shifts;
}

function SeriesTooltip({
  row,
  series,
  tooltipRow,
  valueFormat,
}: {
  row: Row | undefined;
  series: readonly ChartSeries[];
  tooltipRow?: ChartTooltipRow;
  valueFormat?: NumberFormatOptions;
}) {
  const format = useFormatter();
  if (!row) return null;
  const value = (key: string) => {
    const v = row[key];
    return typeof v === 'number' ? format.number(v, valueFormat) : String(v ?? '');
  };
  const tooltipRowValue = tooltipRow ? row[tooltipRow.dataKey] : undefined;
  return (
    <div className="bg-ground text-ink flex min-w-40 flex-col gap-1.5 rounded-lg border p-3 text-xs shadow-md">
      <span className="font-bold">{String(row.label)}</span>
      {series.map((line) => (
        <div key={line.dataKey} className="flex items-center gap-2">
          <span
            aria-hidden
            className={cn('h-0.5 w-4', line.dashed && 'h-0 border-t-2 border-dashed')}
            style={{
              [line.dashed ? 'borderColor' : 'backgroundColor']: `var(--color-${line.dataKey})`,
            }}
          />
          <span className="text-muted grow">{line.label}</span>
          <span className="font-semibold tabular-nums">{value(line.dataKey)}</span>
        </div>
      ))}
      {tooltipRow && (
        <div className="flex items-center gap-2 border-t pt-1.5">
          <span className="text-muted grow">{tooltipRow.label}</span>
          <span
            className={cn(
              'font-semibold tabular-nums',
              typeof tooltipRowValue === 'number' && tooltipRowValue < 0 && 'text-negative',
            )}
          >
            {value(tooltipRow.dataKey)}
          </span>
        </div>
      )}
    </div>
  );
}

/**
 * Multi-series line chart — several named lines over one shared X axis.
 *
 * Two quantities sharing an axis ("sends and replies per day") have to arrive
 * as one row per date with a column each, and that is the shape here.
 *
 * Rows are `Record<string, string | number>` rather than a generic. recharts
 * indexes them by string key at runtime, and every stricter typing of this
 * either misdescribed what recharts accepts or forced a cast at the call site.
 * The `series` array carries the meaning, and it IS typed.
 *
 * `monotone` interpolation, not `linear`: with sparse daily counts a cardinal
 * spline overshoots between points and draws values the data does not contain.
 * Monotone cannot — it is the interpolation that never leaves the interval
 * between two neighbouring points.
 *
 * No `domain` on the Y axis. Pinning a maximum silently clips every value above
 * it, which is the one failure mode a chart must not have.
 */
export function MultiLineChart({
  data,
  series,
  ariaLabel,
  valueFormat,
  tooltipRow,
  className,
}: {
  /** One row per X position. `label` is the axis value; each series reads its own key. */
  data: readonly Row[];
  series: readonly ChartSeries[];
  ariaLabel: string;
  /** How the tooltip formats numbers. Serializable, so a server component can pass it. */
  valueFormat?: NumberFormatOptions;
  tooltipRow?: ChartTooltipRow;
  className?: string;
}) {
  const config: ChartConfig = {};
  for (const line of series) config[line.dataKey] = { label: line.label, color: line.color };
  const shifts = endLabelShifts(series, data.at(-1));

  return (
    <div role="img" aria-label={ariaLabel}>
      <ChartContainer config={config} className={cn('h-72 w-full', className)}>
        {/* Recharts 3 turns the layer on by default, which makes the svg a tab stop
         * inside role="img", whose children are presentational. The table view is
         * the keyboard way to the values. */}
        <RechartsLineChart
          accessibilityLayer={false}
          data={[...data]}
          margin={{ left: 8, right: 84, top: 8 }}
        >
          {/* Horizontal rules only. With a date axis the verticals duplicate the
           * ticks and turn a sparse series into graph paper. */}
          <CartesianGrid vertical={false} />
          <XAxis
            dataKey="label"
            tickLine={false}
            axisLine={false}
            tickMargin={8}
            minTickGap={24}
            tickFormatter={(value: string) => truncateLabel(value, 11)}
          />
          <YAxis
            tickLine={false}
            axisLine={false}
            tickMargin={8}
            width={40}
            allowDecimals={false}
          />
          <ChartTooltip
            cursor={false}
            content={(p) => (
              <SeriesTooltip
                row={p.active ? p.payload?.[0]?.payload : undefined}
                series={series}
                tooltipRow={tooltipRow}
                valueFormat={valueFormat}
              />
            )}
          />
          {series.map((line) => (
            <Line
              key={line.dataKey}
              dataKey={line.dataKey}
              type="monotone"
              // `ChartContainer` publishes each config entry's colour as
              // `--color-<key>`, so the stroke follows the theme rather than
              // being baked in at render.
              stroke={`var(--color-${line.dataKey})`}
              strokeWidth={2}
              strokeDasharray={line.dashed ? '5 4' : undefined}
              dot={false}
            >
              <LabelList
                dataKey={line.dataKey}
                content={({ x, y, index }) =>
                  index === data.length - 1 ? (
                    <text
                      x={Number(x) + END_LABEL_OFFSET}
                      y={Number(y) + (shifts[line.dataKey] ?? 0)}
                      dominantBaseline="central"
                      className="fill-ink text-xs font-semibold"
                    >
                      {line.label}
                    </text>
                  ) : null
                }
              />
            </Line>
          ))}
        </RechartsLineChart>
      </ChartContainer>
    </div>
  );
}
