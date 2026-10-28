// @vitest-environment happy-dom
import { NextIntlClientProvider } from 'next-intl';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vite-plus/test';
import { setSenderPausedAction } from '@/app/(app)/settings/senders/actions';
import { toast } from '@/components/ui/sonner';
import { FORMATS } from '@/i18n/config';
import { unreachable } from '@/lib/api/error';
import { fail, type Result, succeed } from '@/lib/types';
import { MESSAGES } from '@/messages';
import { SenderControls } from './sender-controls';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
vi.mock('@/components/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@/app/(app)/settings/senders/actions', () => ({ setSenderPausedAction: vi.fn() }));

async function mount(paused: boolean) {
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(
      <NextIntlClientProvider locale="en" messages={MESSAGES.en} formats={FORMATS} timeZone="UTC">
        <SenderControls
          senderId="s1"
          address="a@b.test"
          paused={paused}
          state={paused ? 'held' : 'ready'}
          reason={paused ? 'paused' : null}
        />
      </NextIntlClientProvider>,
    );
  });
  return { host, unmount: () => act(async () => root.unmount()) };
}

describe('sender controls (#935)', () => {
  it('shows the account as ready the moment it is resumed, before the page refreshes', async () => {
    let answer: (result: Result<unknown>) => void = () => {};
    vi.mocked(setSenderPausedAction).mockReturnValueOnce(new Promise((r) => (answer = r)));
    const { host, unmount } = await mount(true);
    expect(host.textContent).toContain('Paused');
    await act(async () => {
      host.querySelector('button')?.click();
    });
    expect(host.textContent).toContain('Ready');
    expect(host.textContent).not.toContain('Paused');
    await act(async () => answer(succeed({})));
    await unmount();
  });

  it('keeps the page and shows only the error toast when a pause fails (#1042)', async () => {
    vi.mocked(setSenderPausedAction).mockResolvedValueOnce(fail(unreachable()));
    const { host, unmount } = await mount(false);
    await act(async () => {
      host.querySelector('button')?.click();
    });
    expect(toast.error).toHaveBeenCalledWith('Could not change the mailbox. Please try again.');
    expect(host.querySelector('button')?.getAttribute('aria-label')).toBe('Pause a@b.test');
    await unmount();
  });
});
