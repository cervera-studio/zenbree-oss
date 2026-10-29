import { createFormatter, createTranslator } from 'next-intl';
import { describe, expect, it } from 'vite-plus/test';
import { FORMATS } from '@/i18n/config';
import { MESSAGES } from '@/messages';
import { reminderMonth, restartDay } from './dates';

const italian = () => ({
  t: createTranslator({
    locale: 'it',
    formats: FORMATS,
    messages: MESSAGES.it,
    namespace: 'inbox',
  }),
  format: createFormatter({ locale: 'it', formats: FORMATS, timeZone: 'Europe/Rome' }),
});

describe('inbox dates', () => {
  it('names the restart day with the article Italian puts before it', () => {
    const { t, format } = italian();
    const paused = (iso: string) =>
      t('autoReply.paused', { name: 'Elena', ...restartDay(format, iso) });
    expect(paused('2026-10-08T07:00:00Z')).toBe(
      "Risposta automatica: la sequenza per Elena è in pausa e riparte l'8 ottobre.",
    );
    expect(paused('2026-10-12T07:00:00Z')).toBe(
      'Risposta automatica: la sequenza per Elena è in pausa e riparte il 12 ottobre.',
    );
    expect(paused('2026-11-01T08:00:00Z')).toContain("riparte l'1 novembre.");
    expect(paused('2026-11-11T08:00:00Z')).toContain("riparte l'11 novembre.");
  });

  it('names the restart day in UTC whatever zone the formatter has', () => {
    const { format } = italian();
    expect(restartDay(format, '2026-10-08T23:30:00Z')).toEqual({ date: '8 ottobre', day: '8' });
  });

  it('names the reminder month, with the year only when it is not this one', () => {
    const { t, format } = italian();
    const now = new Date('2026-10-07T10:00:00Z');
    expect(
      t('reminder.set', { name: 'Chiara', month: reminderMonth(format, '2027-01-01', now) }),
    ).toBe('Ho creato un promemoria per gennaio 2027. La sequenza per Chiara si è fermata.');
    expect(t('reminder.setNoSequence', { month: 'gennaio 2027' })).toBe(
      'Ho creato un promemoria per gennaio 2027.',
    );
    expect(reminderMonth(format, '2026-12-01', now)).toBe('dicembre');
  });
});
