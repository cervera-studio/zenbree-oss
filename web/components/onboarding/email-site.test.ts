import { describe, expect, it } from 'vite-plus/test';
import { siteFromEmail } from './email-site';

describe('siteFromEmail', () => {
  it('names the company domain of a work email', () => {
    expect(siteFromEmail('ada@acme.it')).toBe('acme.it');
    expect(siteFromEmail('Ada.Lovelace@Sales.ACME.co.uk ')).toBe('sales.acme.co.uk');
  });

  it('is empty for free mail, wherever its country domain', () => {
    for (const email of [
      'a@gmail.com',
      'a@outlook.it',
      'a@yahoo.co.uk',
      'a@libero.it',
      'a@gmx.de',
    ]) {
      expect(siteFromEmail(email), email).toBe('');
    }
  });

  it('is empty without a usable email', () => {
    expect(siteFromEmail(null)).toBe('');
    expect(siteFromEmail(undefined)).toBe('');
    expect(siteFromEmail('')).toBe('');
    expect(siteFromEmail('ada@localhost')).toBe('');
  });
});
