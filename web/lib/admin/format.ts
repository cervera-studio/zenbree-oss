import type { NumberFormatOptions } from 'next-intl';
import { dayRelation } from '@/lib/chat/home-overview';

// Pure display helpers for the backoffice. `locale` is the next-intl locale.

/** The admin who reads the date: next-intl's locale and time zone (`getLocale`, `getTimeZone`). */
type Reader = { locale: string; timeZone: string };

type Currency = 'USD' | 'EUR';

/** The one money format of the back office: the narrow symbol, at least 2 decimals. */
export function moneyOptions(currency: Currency, maxDigits = 2): NumberFormatOptions {
  return {
    style: 'currency',
    currency,
    currencyDisplay: 'narrowSymbol',
    minimumFractionDigits: 2,
    maximumFractionDigits: maxDigits,
  };
}

export function formatMoney(amount: number, currency: Currency, locale: string): string {
  // Sub-cent LLM costs are common, so keep 4 decimals below 1.
  const digits = Math.abs(amount) < 1 && amount !== 0 ? 4 : 2;
  return new Intl.NumberFormat(locale, moneyOptions(currency, digits)).format(amount);
}

/** Costs are kept in USD and revenue in EUR; each formatter puts its symbol where `locale` does. */
export function moneyFormatters(locale: string) {
  return {
    eur: (n: number) => formatMoney(n, 'EUR', locale),
    usd: (n: number) => formatMoney(n, 'USD', locale),
  };
}

/** margin / revenue; null when there is no revenue to divide by. */
export function marginRatio(margin: number, revenue: number): number | null {
  return revenue === 0 ? null : margin / revenue;
}

export function formatMarginPct(margin: number, revenue: number, locale: string): string {
  const ratio = marginRatio(margin, revenue);
  if (ratio === null) return '—';
  return new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: 1 }).format(
    ratio,
  );
}

export function formatDate(iso: string | null | undefined, { locale, timeZone }: Reader): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat(locale, {
    dateStyle: 'short',
    timeStyle: 'short',
    timeZone,
  }).format(d);
}

export function formatDuration(start: string | null, end: string | null): string {
  if (!start || !end) return '—';
  const seconds = Math.floor((Date.parse(end) - Date.parse(start)) / 1000);
  if (!Number.isFinite(seconds) || seconds < 0) return '—';
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = String(seconds % 60).padStart(2, '0');
  return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${rest}` : `${minutes}:${rest}`;
}

/** A calendar day, e.g. «14 set 2026». */
export function formatDay(iso: string | Date, { locale, timeZone }: Reader): string {
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeZone }).format(new Date(iso));
}

/** «Oggi, 11:42» / «Ieri, 18:04» within a day or two, otherwise the day («30 set»). */
export function formatLastSeen(iso: string, { locale, timeZone }: Reader, now: Date): string {
  const d = new Date(iso);
  const relation = dayRelation(d, now, timeZone);
  if (relation !== 'earlier') {
    const word = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' }).format(
      relation === 'today' ? 0 : -1,
      'day',
    );
    const time = new Intl.DateTimeFormat(locale, { timeStyle: 'short', timeZone }).format(d);
    return `${word.charAt(0).toUpperCase()}${word.slice(1)}, ${time}`;
  }
  const year = (x: Date) =>
    new Intl.DateTimeFormat('en-US', { year: 'numeric', timeZone }).format(x);
  return new Intl.DateTimeFormat(locale, {
    day: 'numeric',
    month: 'short',
    timeZone,
    ...(year(d) !== year(now) && { year: 'numeric' }),
  }).format(d);
}
