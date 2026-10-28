import { describe, expect, it, vi } from 'vite-plus/test';
import requestConfig from './request';

let cookie: string | undefined;
let localeCookie: string | undefined;
let acceptLanguage: string | undefined;
let signedIn = false;
let saved: string | null = null;
let savedLocale: string | undefined;
vi.mock('next/headers', () => ({
  cookies: async () => ({
    get: (name: string) => {
      const value = { NEXT_TIME_ZONE: cookie, NEXT_LOCALE: localeCookie }[name];
      return value ? { value } : undefined;
    },
    getAll: () => (signedIn ? [{ name: 'better-auth.session_token', value: 'x' }] : []),
  }),
  headers: async () => new Headers(acceptLanguage ? { 'accept-language': acceptLanguage } : {}),
}));
// Outside a server component next-intl refuses getRequestConfig; on the server it is the identity.
vi.mock('@/lib/auth-token', () => ({
  getSession: async () => ({ user: { timeZone: saved, locale: savedLocale } }),
}));
vi.mock('next-intl/server', () => ({ getRequestConfig: <T>(load: T) => load }));

describe('request time zone', () => {
  it.each([
    ['Europe/Rome', 'Europe/Rome'],
    ['Asia/Calcutta', 'Asia/Calcutta'],
    ['Mars/Olympus', 'UTC'],
    ['<script>', 'UTC'],
    ['', 'UTC'],
    [undefined, 'UTC'],
  ])('cookie %s gives %s', async (value, zone) => {
    cookie = value;
    const config = await requestConfig({ requestLocale: Promise.resolve(undefined) });
    expect(config.timeZone).toBe(zone);
  });
});

describe('saved time zone', () => {
  it.each([
    [true, 'America/New_York', 'Europe/Rome', 'America/New_York'],
    [true, null, 'Europe/Rome', 'Europe/Rome'],
    [true, 'Mars/Olympus', 'Europe/Rome', 'Europe/Rome'],
    [false, 'America/New_York', 'Europe/Rome', 'Europe/Rome'],
  ])('signed in %s, saved %s, cookie %s gives %s', async (isIn, zone, browser, expected) => {
    signedIn = isIn;
    saved = zone;
    cookie = browser;
    const config = await requestConfig({ requestLocale: Promise.resolve(undefined) });
    expect(config.timeZone).toBe(expected);
    signedIn = false;
    saved = null;
  });
});

describe('request language', () => {
  it.each([
    // An existing Italian user on a German browser, with no cookie: their saved language, not the default.
    [true, 'it', undefined, 'de-CH', 'it'],
    // A user saved in English on a French browser stays English.
    [true, 'en', undefined, 'fr-FR,fr,en', 'en'],
    [true, 'es', undefined, undefined, 'es'],
    // The browser's own choice wins over the saved one; the bell stream then saves it.
    [true, 'it', 'fr', 'de-CH', 'fr'],
    // An unknown saved value falls through to the browser.
    [true, 'de', undefined, 'es-ES', 'es'],
    // A cookie no language matches (an old value, a language since removed) is no choice: the saved one.
    [true, 'it', 'de', 'en-US', 'it'],
    // No session: the cookie, then the browser, then English.
    [false, 'it', undefined, 'fr-FR', 'fr'],
    [false, 'it', undefined, 'de-CH', 'en'],
  ])(
    'signed in %s, saved %s, cookie %s, browser %s gives %s',
    async (isIn, stored, choice, browser, expected) => {
      signedIn = isIn;
      savedLocale = stored;
      localeCookie = choice;
      acceptLanguage = browser;
      const config = await requestConfig({ requestLocale: Promise.resolve(undefined) });
      expect(config.locale).toBe(expected);
      signedIn = false;
      savedLocale = undefined;
      localeCookie = undefined;
      acceptLanguage = undefined;
    },
  );
});
