import { describe, expect, it } from 'vite-plus/test';
import { LOCALES } from '@/i18n/config';
import { formatNumber, parseNumber } from './number';

describe('parseNumber', () => {
  it.each([
    ['it', '1.000', 1000],
    ['it', '1,000', 1],
    ['it', '1.000,5', 1000.5],
    ['it', '12,5', 12.5],
    ['it', '1.5', null],
    ['it', '1.000.000', 1_000_000],
    ['it', '-3,5', -3.5],
    ['en', '1.000', 1],
    ['en', '1,000', 1000],
    ['en', '1.000,5', null],
    ['en', '12,5', null],
    ['en', '1,000.5', 1000.5],
    ['es', '1.000,5', 1000.5],
    ['fr', '1 000,5', 1000.5],
    ['fr', '1 000', 1000],
    ['it', '', null],
    ['it', 'abc', null],
    ['it', '10%', null],
  ])('reads %s %j as %j', (locale, raw, expected) => {
    expect(parseNumber(raw, locale)).toBe(expected);
  });

  it.each(LOCALES)('reads back what it writes in %s', (locale) => {
    for (const n of [0, 80.5, -3.25, 1234567, 0.1]) {
      expect(parseNumber(formatNumber(n, locale), locale)).toBe(n);
    }
  });
});
