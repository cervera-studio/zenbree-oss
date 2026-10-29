// @vitest-environment happy-dom
import { NextIntlClientProvider } from 'next-intl';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vite-plus/test';
import { disconnectMailboxAction } from '@/app/(app)/settings/integrations/actions';
import { FORMATS } from '@/i18n/config';
import { succeed } from '@/lib/types';
import { MESSAGES } from '@/messages';
import { MailboxRow } from './mailbox-row';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock('@/components/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@/app/(app)/settings/integrations/actions', () => ({
  connectMailboxAction: vi.fn(),
  disconnectMailboxAction: vi.fn(),
}));

const buttons = (name: string) =>
  [...document.body.querySelectorAll('button')].filter((b) => b.textContent === name);

describe('mailbox rows (#935)', () => {
  it('disconnects the account of its own row among two of one provider', async () => {
    vi.mocked(disconnectMailboxAction).mockResolvedValue(succeed({}));
    const host = document.createElement('div');
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => {
      root.render(
        <NextIntlClientProvider locale="en" messages={MESSAGES.en} formats={FORMATS} timeZone="UTC">
          {['a@acme.test', 'b@acme.test'].map((label, i) => (
            <MailboxRow
              key={label}
              id={`box-${i}`}
              provider="google"
              accountLabel={label}
              connected
              disabled={false}
              canDisconnect
              canReadBounces
            />
          ))}
        </NextIntlClientProvider>,
      );
    });

    await act(async () => buttons('Disconnect')[1]?.click());
    await act(async () => buttons('Disconnect').at(-1)?.click());

    expect(disconnectMailboxAction).toHaveBeenCalledExactlyOnceWith('box-1');
    await act(async () => root.unmount());
  });
});
