'use client';

import { useLocale, useTranslations } from 'next-intl';
import { useTransition } from 'react';
import { setLocaleAction } from '@/app/(app)/settings/actions';
import { LOCALE_NAMES, LOCALES } from '@/i18n/config';
import { cn } from '@/lib/utils';

export function LocaleSwitch() {
  const t = useTranslations('auth');
  const current = useLocale();
  const [pending, startTransition] = useTransition();
  return (
    <fieldset className="inline-flex gap-2">
      <legend className="sr-only">{t('language')}</legend>
      {LOCALES.map((locale) => (
        <button
          key={locale}
          type="button"
          lang={locale}
          // Spelled out so a screen reader doesn't read "IT" as the English pronoun.
          aria-label={LOCALE_NAMES[locale]}
          aria-pressed={locale === current}
          disabled={pending}
          onClick={() => startTransition(() => setLocaleAction(locale))}
          className={cn(
            'text-small inline-flex h-control-sm items-center rounded-md border px-2.25 uppercase outline-none focus-visible:ring-2 focus-visible:ring-accent',
            locale === current
              ? 'border-accent-tint bg-accent-tint text-accent-ink'
              : 'border-line text-soft hover:bg-raised',
          )}
        >
          {locale}
        </button>
      ))}
    </fieldset>
  );
}
