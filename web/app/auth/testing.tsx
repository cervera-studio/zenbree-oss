import { NextIntlClientProvider } from 'next-intl';
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { onTestFinished } from 'vite-plus/test';
import { FORMATS, type Locale } from '@/i18n/config';
import { MESSAGES } from '@/messages';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

/** The app's i18n context for a test: Italian unless asked, the app number formats, a fixed zone. */
export function TestIntl({
  children,
  timeZone = 'Europe/Rome',
  locale = 'it',
}: {
  children: ReactNode;
  timeZone?: string;
  locale?: Locale;
}) {
  return (
    <NextIntlClientProvider
      locale={locale}
      messages={MESSAGES[locale]}
      formats={FORMATS}
      timeZone={timeZone}
    >
      {children}
    </NextIntlClientProvider>
  );
}

let mounted: Root | undefined;

// onTestFinished, not a module-level afterEach: observed here, an afterEach registered by this imported module ran only after the last test of the file.
async function unmountMounted() {
  const root = mounted;
  mounted = undefined;
  await act(async () => root?.unmount());
}

/** Renders `ui` in Italian, or in `locale`, into a fresh document body. The previous render is unmounted first, so its effects and timers stop. */
export async function render(ui: ReactNode, locale: Locale = 'it'): Promise<HTMLElement> {
  const previous = mounted;
  await act(async () => previous?.unmount());
  onTestFinished(unmountMounted);
  const container = document.createElement('div');
  document.body.replaceChildren(container);
  const root = createRoot(container);
  mounted = root;
  await act(async () => {
    root.render(<TestIntl locale={locale}>{ui}</TestIntl>);
  });
  return container;
}

/** The form control a visible <label> names. */
export function field(container: HTMLElement, label: string): HTMLInputElement {
  const el = [...container.querySelectorAll('label')].find((l) => l.textContent === label);
  const input = el && container.querySelector(`#${el.htmlFor}`);
  if (!(input instanceof HTMLInputElement)) throw new Error(`no field labelled ${label}`);
  return input;
}

/** A button or link by its accessible name: aria-label first, else its text. */
export function control(container: HTMLElement, name: string): HTMLElement {
  const el = [...container.querySelectorAll<HTMLElement>('button, a')].find(
    (b) => (b.getAttribute('aria-label') ?? b.textContent) === name,
  );
  if (!el) throw new Error(`no control named ${name}`);
  return el;
}

export async function click(el: HTMLElement) {
  await act(async () => el.click());
}

/** Escape on `el`, then the tick in which a closing Radix dialog moves focus. */
export async function pressEscape(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  });
  await act(() => new Promise((resolve) => setTimeout(resolve, 0)));
}
