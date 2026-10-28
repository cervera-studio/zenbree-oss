// @vitest-environment happy-dom
import { useFormatter } from 'next-intl';
import { act } from 'react';
import { createRoot, hydrateRoot } from 'react-dom/client';
import { renderToString } from 'react-dom/server';
import { afterEach, expect, it, vi } from 'vite-plus/test';
import { TestIntl } from '@/app/auth/testing';
import { BrowserTimeZone, SavedTimeZoneProvider } from './browser-time-zone';
import { validTimeZone } from './time-zone';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

afterEach(() => {
  vi.restoreAllMocks();
});

const resolved = Intl.DateTimeFormat.prototype.resolvedOptions;
function mockProcessTimeZone(timeZone: string) {
  vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockImplementation(function (
    this: Intl.DateTimeFormat,
  ) {
    return { ...resolved.call(this), timeZone };
  });
}

function Clock() {
  const format = useFormatter();
  return <time>{format.dateTime(new Date('2026-10-08T11:02:00Z'), { timeStyle: 'short' })}</time>;
}

const tree = (
  <TestIntl timeZone="UTC">
    <BrowserTimeZone>
      <Clock />
    </BrowserTimeZone>
  </TestIntl>
);

it("hydrates the server's markup, then shows the browser's zone", async () => {
  mockProcessTimeZone('UTC');
  const container = document.createElement('div');
  container.innerHTML = renderToString(tree);
  expect(container.textContent).toBe('11:02');
  const serverTime = container.querySelector('time');

  vi.restoreAllMocks();
  mockProcessTimeZone('Europe/Rome');
  const consoleError = vi.spyOn(console, 'error');
  const recoverable = vi.fn();
  await act(async () => {
    hydrateRoot(container, tree, { onRecoverableError: recoverable });
  });

  expect(recoverable).not.toHaveBeenCalled();
  expect(consoleError).not.toHaveBeenCalled();
  expect(container.textContent).toBe('13:02');
  expect(container.querySelector('time'), 'the zone switch must not remount the app').toBe(
    serverTime,
  );
});

it('shows the saved zone over the browser one, and none saved leaves the browser one', async () => {
  mockProcessTimeZone('Europe/Rome');
  const show = async (zone: string | null) => {
    const container = document.createElement('div');
    await act(async () => {
      createRoot(container).render(
        <TestIntl timeZone="UTC">
          <BrowserTimeZone>
            <SavedTimeZoneProvider zone={zone}>
              <Clock />
            </SavedTimeZoneProvider>
          </BrowserTimeZone>
        </TestIntl>,
      );
    });
    return container.textContent;
  };
  expect(await show('America/New_York')).toBe('07:02');
  expect(await show(null)).toBe('13:02');
});

it('accepts only known IANA zones', () => {
  expect(validTimeZone('Europe/Rome')).toBe('Europe/Rome');
  expect(validTimeZone('UTC')).toBe('UTC');
  expect(validTimeZone('Asia/Kolkata')).not.toBeNull();
  expect(validTimeZone('Asia/Kolkata')).toBe(validTimeZone('Asia/Calcutta'));
  expect(validTimeZone('Europe/Kyiv')).toBe(validTimeZone('Europe/Kiev'));
  for (const bad of ['Mars/Olympus', '+05:30', 'EST', 'europe/rome', 'x'.repeat(100)])
    expect(validTimeZone(bad)).toBeNull();
  expect(validTimeZone('')).toBeNull();
  expect(validTimeZone(null)).toBeNull();
});
