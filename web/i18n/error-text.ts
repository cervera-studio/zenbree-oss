import { useTranslations } from 'next-intl';
import { useCallback } from 'react';

type Coded = { code?: string | null; data?: Record<string, unknown> } | null | undefined;
type Translate = ReturnType<typeof useTranslations<'errors'>>;

// A dotted code is the key path under `errors`: `LIST.NOT_FOUND` reads `errors.LIST.NOT_FOUND`.
const CODE = /^[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*$/;
// Transport and request codes do not say what failed; the caller's own text says more.
const VAGUE = /^(INTERNAL|REQUEST)\./;

/**
 * What the user reads for a failure: the text for its code, else the caller's `fallback`, else
 * `errors.generic`. Never the error's `message`, which is the API's English for logs.
 * On the server: `errorText(await getTranslations('errors'), error, fallback)`.
 */
export function errorText(t: Translate, error: Coded, fallback?: string): string {
  const code = error?.code ?? '';
  const known = CODE.test(code) && t.has(code as 'generic');
  if (!known || (fallback && VAGUE.test(code))) return fallback ?? t('generic');
  const text = t(code as 'generic');
  // A campaign step error names its step (`detail.step`), or the user checks every step by hand.
  const step = error?.data?.step;
  return typeof step === 'number' ? t('atStep', { step, text }) : text;
}

export function useErrorText(): (error: Coded, fallback?: string) => string {
  const t = useTranslations('errors');
  return useCallback((error, fallback) => errorText(t, error, fallback), [t]);
}

/** Better Auth's codes (`INVALID_EMAIL_OR_PASSWORD`) live under `errors.AUTH_CLIENT`; a dotted one
 * came from the API through an auth hook (`ACCOUNT.LAST_ADMIN`) and keeps its own place. */
export function authError(error: { code?: string; status?: number } | null): Coded {
  if (!error) return null;
  if (error.code)
    return { code: error.code.includes('.') ? error.code : `AUTH_CLIENT.${error.code}` };
  return { code: error.status ? `REQUEST.HTTP_${error.status}` : undefined };
}
