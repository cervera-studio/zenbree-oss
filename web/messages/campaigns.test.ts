import { createTranslator } from 'next-intl';
import { describe, expect, it } from 'vite-plus/test';
import { FORMATS, type Locale } from '@/i18n/config';
import { MESSAGES } from '.';

const t = (locale: Locale) =>
  createTranslator({
    locale,
    messages: MESSAGES[locale],
    namespace: 'campaigns',
    formats: FORMATS,
  });

const counts = (n: number, many: number) => ({ targets: many, sent: n, replied: n, failed: n });

describe('campaign counts', () => {
  it.each([
    ['it', counts(1, 1), '1 persona · 1 inviato · 1 risposta · 1 non riuscito'],
    ['it', counts(2, 1234), '1.234 persone · 2 inviati · 2 risposte · 2 non riusciti'],
    ['en', counts(1, 1), '1 person · 1 sent · 1 replied · 1 failed'],
    ['en', counts(2, 1234), '1,234 people · 2 sent · 2 replied · 2 failed'],
  ] as const)('the %s header reads %j as «%s»', (locale, values, text) => {
    expect(t(locale)('stats', values)).toBe(text);
  });

  it.each([
    ['it', 1, '1 inviato · 1 non riuscito'],
    ['it', 1234, '1.234 inviati · 1.234 non riusciti'],
    ['en', 1, '1 sent · 1 failed'],
    ['en', 1234, '1,234 sent · 1,234 failed'],
  ] as const)('the %s list card reads %d as «%s»', (locale, n, text) => {
    expect(t(locale)('progress', { sent: n, failed: n })).toBe(text);
  });
});
