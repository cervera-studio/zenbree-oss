'use client';

import { Lock } from 'lucide-react';
import { useFormatter, useTranslations } from 'next-intl';
import { useId, useState } from 'react';
import { SettingsCard } from '@/components/settings/settings-card';
import { Checkbox } from '@/components/ui/checkbox';
import { Select } from '@/components/ui/select';
import { toast } from '@/components/ui/sonner';
import { Switch } from '@/components/ui/switch';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { api } from '@/lib/api/browser';
import { createAutosaver } from '@/lib/notifications/autosave';
import type { PushConfig } from '@/lib/notifications/push-schema';
import { DIGEST_FREQUENCIES, type NotificationSettings } from '@/lib/notifications/settings';
import { BrowserCard, useBrowserPush } from './browser-card';

type Channel = 'email' | 'browser';

const GROUPS = ['replies', 'searches', 'account'] as const;
type Group = (typeof GROUPS)[number];

type ChannelKey =
  | 'replyUrgent'
  | 'reply'
  | 'linkedinAccepted'
  | 'approvalNeeded'
  | 'searchDone'
  | 'searchFailed';
type BrowserKey = 'bouncesHigh' | 'lowCredits';

type EventRow =
  | { kind: 'channels'; key: ChannelKey; group: Group }
  | { kind: 'browser'; key: BrowserKey; group: Group }
  | { kind: 'fixed'; key: 'mailboxError' | 'locked'; group: Group };

const EVENT_TABLE: readonly EventRow[] = [
  { kind: 'channels', key: 'replyUrgent', group: 'replies' },
  { kind: 'channels', key: 'reply', group: 'replies' },
  { kind: 'channels', key: 'linkedinAccepted', group: 'replies' },
  { kind: 'channels', key: 'approvalNeeded', group: 'searches' },
  { kind: 'channels', key: 'searchDone', group: 'searches' },
  { kind: 'channels', key: 'searchFailed', group: 'searches' },
  { kind: 'browser', key: 'bouncesHigh', group: 'searches' },
  { kind: 'browser', key: 'lowCredits', group: 'account' },
  { kind: 'fixed', key: 'mailboxError', group: 'account' },
  { kind: 'fixed', key: 'locked', group: 'account' },
];

function ChannelCell({ children }: { children: React.ReactNode }) {
  return <TableCell className="text-center">{children}</TableCell>;
}

function AlwaysCell() {
  const t = useTranslations('notifications.settings.table');
  return (
    <ChannelCell>
      <span className="text-muted text-small inline-flex items-center gap-1">
        <Lock aria-hidden className="size-3" />
        {t('always')}
      </span>
    </ChannelCell>
  );
}

function UnavailableCell() {
  const t = useTranslations('notifications.settings.table');
  return (
    <ChannelCell>
      <span role="img" aria-label={t('unavailable')} className="text-muted">
        —
      </span>
    </ChannelCell>
  );
}

function EventLine({ row, children }: { row: EventRow; children: React.ReactNode }) {
  const t = useTranslations('notifications.settings');
  return (
    <TableRow>
      <TableCell className="whitespace-normal desktop:min-w-44">
        <span className="flex flex-col py-1.5">
          <span className="text-ui-strong text-ink">{t(`events.${row.key}.label`)}</span>
          <span className="text-muted text-small">{t(`events.${row.key}.help`)}</span>
        </span>
      </TableCell>
      <AlwaysCell />
      {children}
    </TableRow>
  );
}

type Toggle = (key: ChannelKey | BrowserKey, channel: Channel, on: boolean) => void;

function EventCells({
  row,
  value,
  browserOn,
  onToggle,
}: {
  row: EventRow;
  value: NotificationSettings;
  browserOn: boolean;
  onToggle: Toggle;
}) {
  const t = useTranslations('notifications.settings');
  const name = (channel: Channel) => `${t(`events.${row.key}.label`)}: ${t(`channels.${channel}`)}`;
  return (
    <>
      {row.kind === 'channels' ? (
        <ChannelCell>
          <Checkbox
            checked={value.events[row.key].email}
            onCheckedChange={(on) => onToggle(row.key, 'email', on === true)}
            aria-label={name('email')}
          />
        </ChannelCell>
      ) : (
        <AlwaysCell />
      )}
      {row.kind === 'fixed' ? (
        <UnavailableCell />
      ) : (
        <ChannelCell>
          <Checkbox
            checked={value.events[row.key].browser && browserOn}
            disabled={!browserOn}
            onCheckedChange={(on) => onToggle(row.key, 'browser', on === true)}
            aria-label={name('browser')}
          />
        </ChannelCell>
      )}
    </>
  );
}

function EventsTable({
  value,
  browserOn,
  onToggle,
}: {
  value: NotificationSettings;
  browserOn: boolean;
  onToggle: Toggle;
}) {
  const t = useTranslations('notifications.settings');
  return (
    <Table
      frame
      className="phone:[&_td:not(:first-child)]:px-1 phone:[&_th:not(:first-child)]:px-1"
    >
      <TableHeader>
        <TableRow>
          <TableHead scope="col">{t('table.event')}</TableHead>
          <TableHead scope="col" className="w-16 text-center desktop:w-24">
            {t('channels.inApp')}
          </TableHead>
          <TableHead scope="col" className="w-16 text-center desktop:w-24">
            {t('channels.email')}
          </TableHead>
          <TableHead scope="col" className="w-16 text-center desktop:w-25">
            {t('channels.browser')}
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {GROUPS.map((group) => (
          <GroupRows key={group} group={group}>
            {EVENT_TABLE.filter((row) => row.group === group).map((row) => (
              <EventLine key={row.key} row={row}>
                <EventCells row={row} value={value} browserOn={browserOn} onToggle={onToggle} />
              </EventLine>
            ))}
          </GroupRows>
        ))}
      </TableBody>
    </Table>
  );
}

function GroupRows({ group, children }: { group: Group; children: React.ReactNode }) {
  const t = useTranslations('notifications.settings.groups');
  return (
    <>
      <TableRow className="bg-raised">
        <TableCell colSpan={4} className="h-7!">
          <span className="text-eyebrow text-muted font-mono uppercase">{t(group)}</span>
        </TableCell>
      </TableRow>
      {children}
    </>
  );
}

const HOURS = Array.from({ length: 24 }, (_, hour) => hour);

function DigestSchedule({
  value,
  onChange,
}: {
  value: NotificationSettings['digest'];
  onChange: (digest: NotificationSettings['digest']) => void;
}) {
  const t = useTranslations('notifications.settings.digest');
  const format = useFormatter();
  const frequencyId = useId();
  const hourId = useId();
  return (
    <div className="phone:flex-wrap flex items-center gap-2">
      <label htmlFor={frequencyId} className="sr-only">
        {t('frequencyLabel')}
      </label>
      <Select
        touch
        id={frequencyId}
        className="min-w-0 flex-1 phone:basis-full"
        value={value.frequency}
        onChange={(e) => {
          const frequency = DIGEST_FREQUENCIES.find((f) => f === e.target.value);
          if (frequency) onChange({ ...value, frequency });
        }}
      >
        {DIGEST_FREQUENCIES.map((frequency) => (
          <option key={frequency} value={frequency}>
            {t(`frequency.${frequency}`)}
          </option>
        ))}
      </Select>
      <span aria-hidden className="text-muted text-small">
        {t('at')}
      </span>
      <label htmlFor={hourId} className="sr-only">
        {t('hourLabel')}
      </label>
      <Select
        touch
        id={hourId}
        className="w-28"
        value={value.hour}
        disabled={value.frequency === 'off'}
        onChange={(e) => onChange({ ...value, hour: Number(e.target.value) })}
      >
        {HOURS.map((hour) => (
          <option key={hour} value={hour}>
            {format.dateTime(new Date(Date.UTC(2000, 0, 1, hour)), {
              hour: 'numeric',
              minute: '2-digit',
              timeZone: 'UTC',
            })}
          </option>
        ))}
      </Select>
    </div>
  );
}

const SAVE_TOAST = 'notification-settings-save';

export function NotificationSettingsForm({
  initial,
  push,
}: {
  initial: NotificationSettings;
  push: PushConfig;
}) {
  const t = useTranslations('notifications.settings');
  const [value, setValue] = useState(initial);
  const browser = useBrowserPush(push);

  const [saver] = useState(() =>
    createAutosaver({
      initial,
      put: async (next) =>
        (await api('/v1/notifications/settings', { method: 'PUT', body: next })).success,
      onChange: setValue,
      onResult: (ok) =>
        ok
          ? toast.success(t('saved'), { id: SAVE_TOAST })
          : toast.error(t('saveError'), { id: SAVE_TOAST }),
    }),
  );

  const setChannel: Toggle = (key, channel, on) =>
    saver.change((v) => ({
      ...v,
      events: { ...v.events, [key]: { ...v.events[key], [channel]: on } },
    }));

  return (
    <div className="grid gap-4 desktop:grid-cols-[minmax(0,1fr)_25rem] desktop:grid-rows-[auto_auto_1fr] desktop:items-start">
      <div className="empty:hidden desktop:col-start-2 desktop:row-start-1">
        <BrowserCard {...browser} />
      </div>
      <div className="desktop:col-start-1 desktop:row-span-3 desktop:row-start-1">
        <EventsTable
          value={value}
          browserOn={browser.permission === 'granted'}
          onToggle={setChannel}
        />
      </div>
      <SettingsCard className="gap-2.5 p-4 desktop:col-start-2 desktop:row-start-2">
        <h3 className="text-ui-strong">{t('digest.label')}</h3>
        <p className="text-soft text-small">{t('digest.help')}</p>
        <DigestSchedule
          value={value.digest}
          onChange={(digest) => saver.change((v) => ({ ...v, digest }))}
        />
      </SettingsCard>
      <SettingsCard className="gap-2.5 p-4 desktop:col-start-2 desktop:row-start-3">
        <h3 className="text-ui-strong">{t('dnd.label')}</h3>
        <p className="text-soft text-small">{t('dnd.help')}</p>
        <div className="flex items-center gap-2.5">
          <Switch
            aria-label={t('dnd.label')}
            checked={value.dnd}
            onCheckedChange={(on) => saver.change((v) => ({ ...v, dnd: on }))}
          />
          <span aria-hidden className="text-ui-strong">
            {t(value.dnd ? 'switch.on' : 'switch.off')}
          </span>
        </div>
      </SettingsCard>
    </div>
  );
}
