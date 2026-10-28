import { describe, expect, it } from 'vite-plus/test';
import { hasRole } from './roles';

describe('hasRole', () => {
  it('matches inside a list', () => expect(hasRole('user, admin', 'admin')).toBe(true));
  it('rejects prefixes and empties', () => {
    expect(hasRole('administrator', 'admin')).toBe(false);
    expect(hasRole(null, 'admin')).toBe(false);
  });
});
