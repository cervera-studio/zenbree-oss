import { describe, expect, it } from 'vite-plus/test';
import { LOCALES, requestLocale, resolveLocale, storedLocale } from './config';

describe('resolveLocale', () => {
  it('prefers the cookie', () => expect(resolveLocale('en', 'it-IT,it;q=0.9')).toBe('en'));
  it('ignores an unknown cookie', () => expect(resolveLocale('de', 'it-IT')).toBe('it'));
  it('takes the first supported header entry', () =>
    expect(resolveLocale(undefined, 'de-DE,it;q=0.8,en;q=0.5')).toBe('it'));
  it('falls back to en', () => {
    expect(resolveLocale(undefined, null)).toBe('en');
    expect(resolveLocale('de', 'de-DE,ja;q=0.8')).toBe('en');
  });
});

describe('requestLocale', () => {
  const headers = (init: Record<string, string>) => new Headers(init);
  it('takes the NEXT_LOCALE cookie over Accept-Language', () =>
    expect(
      requestLocale(headers({ cookie: 'a=1; NEXT_LOCALE=en; b=2', 'accept-language': 'it-IT' })),
    ).toBe('en'));
  it('takes Accept-Language without the cookie', () =>
    expect(requestLocale(headers({ 'accept-language': 'en-GB,en;q=0.8' }))).toBe('en'));
  it('falls back to en with neither', () => {
    expect(requestLocale(headers({}))).toBe('en');
    expect(requestLocale(undefined)).toBe('en');
  });
});

describe('storedLocale', () => {
  it('reads a supported language, and anything else as en', () => {
    expect(LOCALES.map(storedLocale)).toEqual(LOCALES);
    expect(['de', '', 'toString', null, undefined, 1].map(storedLocale)).toEqual(
      Array(6).fill('en'),
    );
  });
});
