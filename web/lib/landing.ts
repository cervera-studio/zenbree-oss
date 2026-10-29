import type { Locale } from '@/i18n/config';

/**
 * A page of the marketing site (`landing/`), or null when `NEXT_PUBLIC_LANDING_URL` is empty or
 * not a URL: callers then keep the visitor in the app. Read inside the function, as the exact
 * `process.env.NEXT_PUBLIC_*` member expression, because Next inlines only that shape.
 */
export function landingUrl(locale: Locale, hash = ''): URL | null {
  const base = process.env.NEXT_PUBLIC_LANDING_URL;
  if (!base) return null;
  try {
    // The landing serves Italian at `/` and each other language at `/<locale>/`
    // (`landing/astro.config.mjs`); it has no other page.
    return new URL(`${locale === 'it' ? '/' : `/${locale}/`}${hash}`, base);
  } catch {
    return null;
  }
}
