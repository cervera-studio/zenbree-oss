import type { Pool } from 'pg';
import { describe, expect, it } from 'vite-plus/test';
import { inviteeLocale } from './team';

const poolWith = (rows: { locale: string }[]) =>
  ({ query: async () => ({ rows }) }) as unknown as Pool;
const inviter = new Request('http://zenbree.test', { headers: { 'accept-language': 'en-US' } });

describe('inviteeLocale', () => {
  it("takes the stored user's locale over the inviter's request", async () => {
    expect(await inviteeLocale(poolWith([{ locale: 'it' }]), 'a@x.test', inviter)).toBe('it');
    expect(await inviteeLocale(poolWith([{ locale: 'en' }]), 'a@x.test', undefined)).toBe('en');
  });

  it("takes the inviter's request locale for an address with no user", async () => {
    expect(await inviteeLocale(poolWith([]), 'a@x.test', inviter)).toBe('en');
    expect(await inviteeLocale(poolWith([]), 'a@x.test', undefined)).toBe('en');
  });
});
