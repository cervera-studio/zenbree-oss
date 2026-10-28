// Each language by its own name, so a switcher reads the same in every language. The keys are the
// supported languages: adding one here makes `messages/index.ts` and the tests ask for its catalogs.
export const LOCALE_NAMES = {
  en: 'English',
  it: 'Italiano',
  es: 'Español',
  fr: 'Français',
} as const;
export type Locale = keyof typeof LOCALE_NAMES;
export const LOCALES = Object.keys(LOCALE_NAMES) as [Locale, ...Locale[]];
// English is the base: the default, the fallback, and the catalog the others are checked against (#699).
const defaultLocale: Locale = 'en';

// Manual locale override cookie; read in request.ts.
export const LOCALE_COOKIE = 'NEXT_LOCALE';

export function isLocale(value: unknown): value is Locale {
  return typeof value === 'string' && Object.hasOwn(LOCALE_NAMES, value);
}

// Cookie override first, then the first supported Accept-Language entry
// (already ordered by q-weight; `it-IT` → `it`), then the default.
export function resolveLocale(cookie: string | undefined, acceptLanguage: string | null): Locale {
  if (isLocale(cookie)) return cookie;
  for (const part of acceptLanguage?.split(',') ?? []) {
    const base = part.trim().split(';')[0]?.toLowerCase().split('-')[0];
    if (isLocale(base)) return base;
  }
  return defaultLocale;
}

export function requestLocale(headers: Headers | undefined): Locale {
  const cookie = headers
    ?.get('cookie')
    ?.split(';')
    .map((c) => c.trim().split('='))
    .find(([name]) => name === LOCALE_COOKIE)?.[1];
  return resolveLocale(cookie, headers?.get('accept-language') ?? null);
}

export const storedLocale = (value: unknown): Locale => (isLocale(value) ? value : defaultLocale);

// Italian Intl skips the thousands separator on 4-digit numbers, so credits counts opt in.
export const FORMATS = { number: { credits: { useGrouping: 'always' } } } as const;
