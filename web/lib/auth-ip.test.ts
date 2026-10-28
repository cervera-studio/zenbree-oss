import { getIP } from 'better-auth/api';
import { describe, expect, it } from 'vite-plus/test';
import { AUTH_IP_ADDRESS } from './auth-ip';

const options = { advanced: { ipAddress: AUTH_IP_ADDRESS } } as Parameters<typeof getIP>[1];
const ipOf = (headers: Record<string, string>) => getIP(new Headers(headers), options);

describe('the client IP of the Better Auth rate limit', () => {
  it('is the Cloudflare client IP, whatever X-Forwarded-For holds', () => {
    expect(
      ipOf({ 'cf-connecting-ip': '203.0.113.9', 'x-forwarded-for': '1.2.3.4, 203.0.113.9' }),
    ).toBe('203.0.113.9');
  });

  it('falls back to a single X-Forwarded-For when there is no Cloudflare header', () => {
    expect(ipOf({ 'x-forwarded-for': '198.51.100.7' })).toBe('198.51.100.7');
  });

  it('skips a Cloudflare header that is not an IP', () => {
    expect(ipOf({ 'cf-connecting-ip': 'nope', 'x-forwarded-for': '198.51.100.7' })).toBe(
      '198.51.100.7',
    );
  });
});
