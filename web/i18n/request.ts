import { cookies, headers } from 'next/headers';
import { getRequestConfig } from 'next-intl/server';
import { getSession } from '@/lib/auth-token';
import { MESSAGES } from '../messages';
import { FORMATS, isLocale, LOCALE_COOKIE, resolveLocale } from './config';
import { resolveTimeZone, TIME_ZONE_COOKIE, validTimeZone } from './time-zone';

// Only a request that carries a session cookie pays for the session lookup.
async function savedUser(store: Awaited<ReturnType<typeof cookies>>) {
  if (!store.getAll().some(({ name }) => name.endsWith('session_token'))) return null;
  try {
    return (await getSession())?.user ?? null;
  } catch {
    return null;
  }
}

export default getRequestConfig(async () => {
  const store = await cookies();
  const user = await savedUser(store);
  // The cookie is this browser's choice; without a valid one, the language saved on the user (#699): their emails,
  // alerts and agent use it, and the bell stream would otherwise overwrite it with whatever the browser asks for.
  const saved =
    user && 'locale' in user && typeof user.locale === 'string' ? user.locale : undefined;
  const cookie = store.get(LOCALE_COOKIE)?.value;
  const locale = resolveLocale(
    isLocale(cookie) ? cookie : saved,
    (await headers()).get('accept-language'),
  );
  return {
    locale,
    messages: MESSAGES[locale],
    formats: FORMATS,
    timeZone: validTimeZone(user?.timeZone) ?? resolveTimeZone(store.get(TIME_ZONE_COOKIE)?.value),
  };
});
