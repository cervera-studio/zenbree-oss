import { describe, expect, it } from 'vite-plus/test';
import { lastDays, localDay, parseRange } from './range';

describe('report range', () => {
  it('reads today in the user zone, not the server zone', () => {
    const at = new Date('2026-04-30T22:30:00Z');
    expect(localDay(at, 'Europe/Rome')).toBe('2026-05-01');
    expect(localDay(at, 'UTC')).toBe('2026-04-30');
  });

  it('counts a preset from today backwards, today included', () => {
    expect(lastDays(7, '2026-03-02')).toEqual({ from: '2026-02-24', to: '2026-03-02' });
  });

  it('falls back to the last 30 days when a day is missing or not a date', () => {
    const last30 = { from: '2026-04-20', to: '2026-05-19' };
    expect(parseRange({}, '2026-05-19')).toEqual(last30);
    expect(parseRange({ from: '2026-02-30', to: '2026-03-01' }, '2026-05-19')).toEqual(last30);
    expect(parseRange({ from: 'yesterday', to: '2026-03-01' }, '2026-05-19')).toEqual(last30);
  });

  it('swaps days in the wrong order and keeps at most 366 days', () => {
    expect(parseRange({ from: '2026-05-10', to: '2026-05-01' }, '2026-05-19')).toEqual({
      from: '2026-05-01',
      to: '2026-05-10',
    });
    expect(parseRange({ from: '2020-01-01', to: '2028-12-31' }, '2026-05-19')).toEqual({
      from: '2028-01-01',
      to: '2028-12-31',
    });
  });
});
