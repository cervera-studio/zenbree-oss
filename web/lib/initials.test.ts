import { describe, expect, it } from 'vite-plus/test';
import { initials } from './initials';

describe('initials', () => {
  it('takes the first letter of the first two words of a name or an email', () => {
    expect(initials('Ada Rossi')).toBe('AR');
    expect(initials('lia.neri@acme.it')).toBe('LN');
  });

  it('marks a row with no name with «?», not an empty circle', () => {
    expect(initials('')).toBe('?');
    expect(initials('  ')).toBe('?');
  });
});
