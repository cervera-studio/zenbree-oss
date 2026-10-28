/** The report's date range (#942): calendar days in the user's zone, both included, as the API takes them. */
export type Range = { from: string; to: string };

export const PRESETS = [7, 30, 90] as const;
const DEFAULT_DAYS = 30;
const MAX_DAYS = 366; // the API refuses a longer range

const DAY = /^\d{4}-\d{2}-\d{2}$/;

/** The calendar day of `at` in `timeZone`, as YYYY-MM-DD. */
export function localDay(at: Date, timeZone: string): string {
  const parts = new Intl.DateTimeFormat('en', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(at);
  const part = (type: string) => parts.find((p) => p.type === type)?.value;
  return `${part('year')}-${part('month')}-${part('day')}`;
}

// A day is a date with no zone, so UTC arithmetic on it never meets a DST change.
function shift(day: string, days: number): string {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

const isDay = (value: string | undefined): value is string =>
  value !== undefined && DAY.test(value) && shift(value, 0) === value;

export function lastDays(days: number, today: string): Range {
  return { from: shift(today, 1 - days), to: today };
}

/** The range the search params ask for, made valid: the last 30 days when either day is missing or
 * not a date, the two days swapped when `to` is first, and at most 366 days ending on `to`. */
export function parseRange(params: { from?: string; to?: string }, today: string): Range {
  const { from, to } = params;
  if (!isDay(from) || !isDay(to)) return lastDays(DEFAULT_DAYS, today);
  const [start, end] = from <= to ? [from, to] : [to, from];
  const earliest = shift(end, 1 - MAX_DAYS);
  return { from: start < earliest ? earliest : start, to: end };
}
