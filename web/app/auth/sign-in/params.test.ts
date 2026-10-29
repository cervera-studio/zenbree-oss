import { describe, expect, it } from 'vite-plus/test';
import { decodeBan, safeCallback, signInPath, signUpPath, verifyPath } from './params';

describe('safeCallback', () => {
  it('keeps same-origin relative paths', () => {
    expect(safeCallback('/?s=abc')).toBe('/?s=abc');
    expect(safeCallback('/lists/1#top')).toBe('/lists/1#top');
  });
  it.each([
    null,
    '',
    'https://evil.com',
    '//evil.com',
    '/\\evil.com',
    'javascript:alert(1)',
    'lists',
    '/.//evil.com',
    '/a/..//evil.com',
    '/..//evil.com',
    '/%2e//evil.com',
    '/./\\evil.com',
    '/x/../\\evil.com',
    '/%2F%2Fevil.com',
    '/%5Cevil.com',
  ])('falls back to / for %s', (raw) => {
    expect(safeCallback(raw)).toBe('/');
  });
  it('falls back to / for a repeated parameter', () => {
    expect(safeCallback(['/a', '/b'])).toBe('/');
  });
  it('rejects control characters', () => {
    expect(safeCallback('/\t/evil.com')).toBe('/');
  });
  it('allows encoded separators in the query only', () => {
    expect(safeCallback('/lists?next=%2Fx')).toBe('/lists?next=%2Fx');
  });
});

describe('signInPath', () => {
  it('builds the plain and expired variants', () => {
    expect(signInPath('/')).toBe('/auth/sign-in');
    expect(signInPath('/?s=abc', true)).toBe(
      '/auth/sign-in?notice=expired&callbackUrl=%2F%3Fs%3Dabc',
    );
  });
  it('carries the same safe return path through sign-up and verify (#323)', () => {
    expect(signUpPath('/invite/abc')).toBe('/auth/sign-up?callbackUrl=%2Finvite%2Fabc');
    expect(verifyPath('/invite/abc')).toBe('/auth/verify?callbackUrl=%2Finvite%2Fabc');
    expect(signUpPath('//evil.test')).toBe('/auth/sign-up');
  });
});

describe('ban info', () => {
  it('degrades to a generic ban on anything else', () => {
    expect(decodeBan('You have been banned')).toEqual({ reason: null, expires: null });
    expect(decodeBan(undefined)).toEqual({ reason: null, expires: null });
    expect(decodeBan('{"reason":1,"expires":"nope"}')).toEqual({ reason: null, expires: null });
  });
});
