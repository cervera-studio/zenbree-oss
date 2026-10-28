// @vitest-environment happy-dom
import { NextIntlClientProvider } from 'next-intl';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vite-plus/test';
import { toast } from '@/components/ui/sonner';
import { FORMATS } from '@/i18n/config';
import { unreachable } from '@/lib/api/error';
import { fail } from '@/lib/types';
import { MESSAGES } from '@/messages';
import { SenderWindow } from './sender-window';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
vi.mock('@/components/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@/app/(app)/settings/senders/actions', () => ({
  setSenderWindowAction: async () => fail(unreachable()),
}));

describe('sender window (#1042)', () => {
  it('keeps the page and shows only the error toast when saving the hours fails', async () => {
    const host = document.createElement('div');
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => {
      root.render(
        <NextIntlClientProvider locale="en" messages={MESSAGES.en} formats={FORMATS} timeZone="UTC">
          <SenderWindow senderId="s1" startHour={9} endHour={18} days={[0]} timeZone="UTC" />
        </NextIntlClientProvider>,
      );
    });
    const save = [...host.querySelectorAll('button')].find((b) => b.textContent === 'Save hours');
    await act(async () => save?.click());
    expect(toast.error).toHaveBeenCalledWith(
      'Could not save the sending hours. Check that the start is before the end and try again.',
    );
    await act(async () => root.unmount());
  });
});
