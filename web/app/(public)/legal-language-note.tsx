import { getLocale, getTranslations } from 'next-intl/server';

const WRITTEN_IN = ['en', 'it'];

// ponytail: the terms and the privacy policy exist in English and Italian only (#699). A reader in
// another language gets the English text with this note, until a lawyer approves a translation.
export async function LegalLanguageNote() {
  const [t, locale] = await Promise.all([getTranslations('public'), getLocale()]);
  if (WRITTEN_IN.includes(locale)) return null;
  return <p className="text-small text-muted">{t('legalLanguageNote')}</p>;
}
