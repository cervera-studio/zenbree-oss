import { describe, expect, it } from 'vite-plus/test';
import { employeeRange } from './criteria';

const num = (n: number) => String(n).replace(/\B(?=(\d{3})+$)/g, '.');
const range = (raw: string) => employeeRange(raw, 'solo', num);

describe('employeeRange', () => {
  it('renders ranges with an en dash and the locale grouping', () => {
    expect(range('1,50')).toBe('1–50');
    expect(range('10,250')).toBe('10–250');
    expect(range('1–50')).toBe('1–50');
    expect(range('1001,5000')).toBe('1.001–5.000');
  });
  it('names a 1-1 range', () => {
    expect(range('1,1')).toBe('solo');
    expect(range('1–1')).toBe('solo');
  });
  it('reads a trailing comma as open-ended and a bare number as itself', () => {
    expect(range('1000,')).toBe('1.000+');
    expect(range('50')).toBe('50');
  });
  it('keeps unknown text', () => {
    expect(range('many')).toBe('many');
    expect(range('1 - 50')).toBe('1 - 50');
  });
});
