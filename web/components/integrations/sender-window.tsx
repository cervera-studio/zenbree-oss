'use client';

import { useFormatter, useTranslations } from 'next-intl';
import { useId, useState } from 'react';
import { setSenderWindowAction } from '@/app/(app)/settings/senders/actions';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useAction } from '@/lib/hooks/use-action';

const DAYS = [0, 1, 2, 3, 4, 5, 6];
const LABEL = 'text-small font-normal text-soft';
// 2024-01-01 is a Monday: the API counts weekdays from Monday = 0.
const monday = (day: number) => new Date(Date.UTC(2024, 0, 1 + day));

/** The hours and weekdays one mailbox sends in (#348), judged in its own time zone. */
export function SenderWindow({
  senderId,
  startHour,
  endHour,
  days,
  timeZone,
}: {
  senderId: string;
  startHour: number;
  endHour: number;
  days: number[];
  timeZone: string;
}) {
  const t = useTranslations('senders.window');
  const format = useFormatter();
  const ids = useId();
  const [start, setStart] = useState(startHour);
  const [end, setEnd] = useState(endHour);
  const [chosen, setChosen] = useState(days);
  const { pending, run } = useAction();
  const valid = start >= 0 && start < end && end <= 24 && chosen.length > 0;

  function save() {
    run(() => setSenderWindowAction(senderId, { startHour: start, endHour: end, days: chosen }), {
      successToast: t('saved'),
      errorToast: t('failed'),
    });
  }

  return (
    <div className="border-line flex flex-col gap-3 rounded-md border p-3">
      <span className="text-ui-strong">{t('title', { zone: timeZone })}</span>
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${ids}-start`} className={LABEL}>
            {t('from')}
          </Label>
          <Input
            id={`${ids}-start`}
            type="number"
            min={0}
            max={23}
            value={start}
            onChange={(event) => setStart(Number(event.target.value))}
            className="w-20"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${ids}-end`} className={LABEL}>
            {t('to')}
          </Label>
          <Input
            id={`${ids}-end`}
            type="number"
            min={1}
            max={24}
            value={end}
            onChange={(event) => setEnd(Number(event.target.value))}
            className="w-20"
          />
        </div>
        <fieldset aria-label={t('days')} className="flex flex-wrap gap-1.5">
          {DAYS.map((day) => (
            <Button
              key={day}
              type="button"
              size="sm"
              variant={chosen.includes(day) ? 'primary' : 'outline'}
              aria-pressed={chosen.includes(day)}
              onClick={() =>
                setChosen((now) =>
                  now.includes(day) ? now.filter((d) => d !== day) : [...now, day],
                )
              }
            >
              {format.dateTime(monday(day), { weekday: 'short', timeZone: 'UTC' })}
            </Button>
          ))}
        </fieldset>
        <Button type="button" size="sm" disabled={pending || !valid} onClick={save}>
          {t('save')}
        </Button>
      </div>
      <span className="text-small text-muted">{t('help')}</span>
    </div>
  );
}
