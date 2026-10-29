import type { useFormatter } from 'next-intl';

type Format = ReturnType<typeof useFormatter>;

/**
 * The day a paused sequence restarts (#346), as «8 ottobre», and its day of the month: the messages pick
 * «l'8» or «il 12» with it (Italian elides the article before 1, 8 and 11).
 */
export function restartDay(format: Format, iso: string) {
  const at = new Date(iso);
  return {
    date: format.dateTime(at, { day: 'numeric', month: 'long', timeZone: 'UTC' }),
    day: format.dateTime(at, { day: 'numeric', timeZone: 'UTC' }),
  };
}

export function reminderMonth(format: Format, remindOn: string, now: Date) {
  const at = new Date(`${remindOn}T00:00:00Z`);
  const year = at.getUTCFullYear() === now.getUTCFullYear() ? undefined : 'numeric';
  return format.dateTime(at, { month: 'long', year, timeZone: 'UTC' });
}
