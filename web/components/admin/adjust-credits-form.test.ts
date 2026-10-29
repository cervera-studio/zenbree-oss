import { describe, expect, it, vi } from 'vite-plus/test';

vi.mock('@/app/admin/actions', () => ({ adjustCredits: vi.fn() }));

import { parseAmount } from './adjust-credits-form';

describe('parseAmount', () => {
  it('reads exponent notation as the number input does', () => {
    expect(parseAmount('1e3')).toBe(1000);
    expect(parseAmount('-50')).toBe(-50);
  });

  it('rejects anything that is not a non-zero integer', () => {
    for (const bad of ['', '0', '1.5', '1e-1', 'abc']) expect(parseAmount(bad)).toBeNull();
  });
});
