export type SendWhen = { day: 'today' | 'tomorrow' | 'weekday'; weekday: string; time: string };

function calendarDay(date: Date, timeZone: string): number {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
  }).formatToParts(date);
  const n = (type: string) => Number(parts.find((p) => p.type === type)?.value);
  return Date.UTC(n('year'), n('month') - 1, n('day')) / 86_400_000;
}

export function sendWhen(sendAt: Date, now: Date, timeZone: string, locale: string): SendWhen {
  const ahead = calendarDay(sendAt, timeZone) - calendarDay(now, timeZone);
  const clock = new Intl.DateTimeFormat('en-GB', {
    timeZone,
    hour: 'numeric',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(sendAt);
  const part = (type: string) => clock.find((p) => p.type === type)?.value ?? '';
  return {
    day: ahead <= 0 ? 'today' : ahead === 1 ? 'tomorrow' : 'weekday',
    weekday: new Intl.DateTimeFormat(locale, { timeZone, weekday: 'long' }).format(sendAt),
    time: `${part('hour')}:${part('minute')}`,
  };
}
