'use client';

import { NextIntlClientProvider, useLocale, useTimeZone } from 'next-intl';
import { createContext, type ReactNode, useContext, useSyncExternalStore } from 'react';
import { browserTimeZone } from './time-zone';

const noop = () => () => {};

const SavedTimeZone = createContext<string | null>(null);

// The zone the user saved in the profile (#348) wins over the browser's.
export function useBrowserTimeZone(): string | null {
  const saved = useContext(SavedTimeZone);
  const browser = useSyncExternalStore(noop, browserTimeZone, () => null);
  return saved ?? browser;
}

export function SavedTimeZoneProvider({
  zone,
  children,
}: {
  zone: string | null;
  children: ReactNode;
}) {
  const locale = useLocale();
  if (!zone) return children;
  return (
    <SavedTimeZone.Provider value={zone}>
      <NextIntlClientProvider locale={locale} timeZone={zone}>
        {children}
      </NextIntlClientProvider>
    </SavedTimeZone.Provider>
  );
}

export function BrowserTimeZone({ children }: { children: ReactNode }) {
  const server = useTimeZone();
  const browser = useBrowserTimeZone();
  return (
    <NextIntlClientProvider locale={useLocale()} timeZone={browser ?? server}>
      {children}
    </NextIntlClientProvider>
  );
}
