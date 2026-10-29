import fc from 'fast-check';
import { describe, expect, it } from 'vite-plus/test';
import { withoutInfraCookie } from './infra-cookie';

describe('withoutInfraCookie', () => {
  it('drops only the infra cookie', () => {
    expect(withoutInfraCookie('a=1; __infra-rid=x; b=2')).toBe('a=1; b=2');
    expect(withoutInfraCookie('__infra-rid=x')).toBeUndefined();
    expect(withoutInfraCookie(null)).toBeUndefined();
  });

  it('never leaves the infra cookie and keeps every other one', () => {
    const name = fc.stringMatching(/^[a-z][a-z0-9_]{0,8}$/).filter((n) => n !== '__infra-rid');
    const pair = fc.tuple(name, fc.stringMatching(/^[a-z0-9]{0,8}$/));
    fc.assert(
      fc.property(fc.array(pair), fc.nat(5), (pairs, at) => {
        const parts = pairs.map(([k, v]) => `${k}=${v}`);
        parts.splice(Math.min(at, parts.length), 0, '__infra-rid=rid');
        const out = withoutInfraCookie(parts.join('; '));
        expect(out ?? '').not.toContain('__infra-rid');
        expect(out?.split('; ') ?? []).toEqual(pairs.map(([k, v]) => `${k}=${v}`));
      }),
    );
  });
});
