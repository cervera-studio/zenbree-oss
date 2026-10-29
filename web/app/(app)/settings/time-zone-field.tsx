'use client';

import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { useState, useSyncExternalStore, useTransition } from 'react';
import { SettingRow } from '@/components/settings/settings-section';
import { Select } from '@/components/ui/select';
import { browserTimeZone, validTimeZone } from '@/i18n/time-zone';
import { setTimeZoneAction } from './actions';

const ZONES = ['UTC', ...Intl.supportedValuesOf('timeZone')];
const noop = () => () => {};

/** The saved time zone; empty falls back to the browser's, which the first option names. */
export function TimeZoneField({ saved }: { saved: string | null }) {
  const t = useTranslations('settings.preferences');
  const router = useRouter();
  // The device's own zone, not the saved one: it is what an empty value falls back to.
  const browser = useSyncExternalStore(noop, browserTimeZone, () => '');
  const [value, setValue] = useState(saved ?? '');
  const [failed, setFailed] = useState(false);
  const [, startTransition] = useTransition();
  // A saved alias that the browser does not list still has to show as the current value.
  const zones = saved && !ZONES.includes(saved) ? [saved, ...ZONES] : ZONES;

  function commit(next: string) {
    setValue(next);
    const zone = next === '' ? null : validTimeZone(next);
    if (next !== '' && zone === null) {
      setFailed(true);
      return;
    }
    if (zone === (saved ?? null)) return;
    setFailed(false);
    startTransition(async () => {
      const ok = await setTimeZoneAction(zone);
      setFailed(!ok);
      router.refresh();
    });
  }

  return (
    <SettingRow
      label={t('timeZone')}
      description={failed ? t('timeZoneInvalid') : t('timeZoneHelp')}
    >
      <Select
        touch
        aria-label={t('timeZone')}
        aria-invalid={failed}
        className="w-55 phone:w-full"
        value={value}
        onChange={(event) => commit(event.target.value)}
      >
        <option value="">
          {browser ? `${t('timeZoneBrowser')} (${browser})` : t('timeZoneBrowser')}
        </option>
        {zones.map((zone) => (
          <option key={zone} value={zone}>
            {zone}
          </option>
        ))}
      </Select>
    </SettingRow>
  );
}
