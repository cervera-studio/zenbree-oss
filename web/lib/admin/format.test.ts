import { describe, expect, it } from 'vite-plus/test';
import {
  formatDate,
  formatDay,
  formatDuration,
  formatLastSeen,
  formatMarginPct,
  formatMoney,
  marginRatio,
} from './format';

// Intl uses narrow no-break spaces; normalize so the assertions stay readable.
const n = (s: string) => s.replace(/\s/g, ' ');

describe('formatMoney', () => {
  it('puts the euro after the amount in Italian', () =>
    expect(n(formatMoney(1234.5, 'EUR', 'it'))).toBe('1234,50 €'));
  it('puts the dollar after the amount in Italian', () =>
    expect(n(formatMoney(2.58, 'USD', 'it'))).toBe('2,58 $'));
  it('keeps sub-cent precision', () => expect(formatMoney(0.0042, 'USD', 'en')).toBe('$0.0042'));
  it('formats zero', () => expect(formatMoney(0, 'USD', 'en')).toBe('$0.00'));
  it('formats negatives', () => expect(formatMoney(-3.5, 'EUR', 'en')).toBe('-€3.50'));
});

describe('margin', () => {
  it('is null with zero revenue', () => expect(marginRatio(-2, 0)).toBeNull());
  it('renders a dash with zero revenue', () => expect(formatMarginPct(-2, 0, 'en')).toBe('—'));
  it('formats a negative margin', () => expect(formatMarginPct(-50, 20, 'en')).toBe('-250%'));
  it('localizes', () => expect(n(formatMarginPct(12.5, 100, 'it'))).toBe('12,5%'));
});

const rome = { locale: 'it', timeZone: 'Europe/Rome' };

describe('formatDate', () => {
  it('handles null', () => expect(formatDate(null, rome)).toBe('—'));
  it('passes through garbage', () => expect(formatDate('nope', rome)).toBe('nope'));
  it('shows the instant in the reader zone', () =>
    expect(n(formatDate('2026-10-04T22:30:00Z', rome))).toBe('05/10/26, 00:30'));
});

describe('formatDay', () => {
  it('shows the calendar day of the reader zone', () =>
    expect(formatDay('2026-10-04T22:30:00Z', rome)).toBe('5 ott 2026'));
});

describe('formatDuration', () => {
  it('shows elapsed time as minutes and seconds', () =>
    expect(formatDuration('2026-10-01T10:00:00Z', '2026-10-01T10:01:02Z')).toBe('1:02'));
  it('shows hours when needed', () =>
    expect(formatDuration('2026-10-01T10:00:00Z', '2026-10-01T11:01:02Z')).toBe('1:01:02'));
  it('shows a dash for unavailable or invalid values', () => {
    expect(formatDuration(null, null)).toBe('—');
    expect(formatDuration('2026-10-01T10:00:00Z', null)).toBe('—');
    expect(formatDuration('bad', '2026-10-01T10:00:00Z')).toBe('—');
  });
});

describe('formatLastSeen', () => {
  const now = new Date('2026-10-05T13:00:00Z');
  it('names today and yesterday with the time', () => {
    expect(formatLastSeen('2026-10-05T09:42:00Z', rome, now)).toBe('Oggi, 11:42');
    expect(formatLastSeen('2026-10-04T16:04:00Z', rome, now)).toBe('Ieri, 18:04');
  });
  it('counts days at midnight in the reader zone, not in UTC', () => {
    expect(formatLastSeen('2026-10-04T22:30:00Z', rome, now)).toBe('Oggi, 00:30');
    expect(formatLastSeen('2026-10-03T22:30:00Z', rome, now)).toBe('Ieri, 00:30');
  });
  it('falls back to the day, with the year only when it differs', () => {
    expect(formatLastSeen('2026-09-30T07:00:00Z', rome, now)).toBe('30 set');
    expect(formatLastSeen('2025-09-30T07:00:00Z', rome, now)).toBe('30 set 2025');
  });
  it('compares years in the reader zone', () =>
    expect(formatLastSeen('2025-12-31T23:30:00Z', rome, new Date('2026-01-05T12:00:00Z'))).toBe(
      '1 gen',
    ));
});
