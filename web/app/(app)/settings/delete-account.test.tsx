// @vitest-environment happy-dom
import { act } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { click, control, field, render } from '@/app/auth/testing';
import { toast } from '@/components/ui/sonner';
import { authClient } from '@/lib/auth-client';
import { MESSAGES } from '@/messages';
import { DeleteAccount } from './delete-account';

const replace = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace }) }));
vi.mock('@/lib/auth-client', () => ({ authClient: { deleteUser: vi.fn() } }));
vi.mock('@/components/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

beforeEach(() => vi.clearAllMocks());

async function type(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  await act(async () => {
    setter?.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

async function openDialog() {
  const c = await render(<DeleteAccount />);
  await click(control(c, 'Elimina account'));
  const dialog = document.querySelector<HTMLElement>('[role="alertdialog"]');
  if (!dialog) throw new Error('no dialog');
  return dialog;
}

describe('DeleteAccount', () => {
  it('deletes only after the confirm word, then leaves for sign-in', async () => {
    vi.mocked(authClient.deleteUser).mockResolvedValue({ data: { success: true }, error: null });
    const dialog = await openDialog();
    const confirm = control(dialog, 'Elimina account');
    const word = field(dialog, 'Scrivi ELIMINA per confermare');

    expect(confirm.hasAttribute('disabled')).toBe(true);
    await type(word, 'elimina');
    expect(confirm.hasAttribute('disabled')).toBe(true);
    await type(word, 'ELIMINA');
    expect(confirm.hasAttribute('disabled')).toBe(false);

    await click(confirm);
    expect(authClient.deleteUser).toHaveBeenCalledOnce();
    expect(replace).toHaveBeenCalledWith('/auth/sign-in');
  });

  it.each([
    ['SESSION_EXPIRED', MESSAGES.it.errors.AUTH_CLIENT.SESSION_EXPIRED],
    ['ACCOUNT.LAST_ADMIN', MESSAGES.it.errors.ACCOUNT.LAST_ADMIN],
    ['ACCOUNT.BILLING_CANCEL_FAILED', MESSAGES.it.errors.ACCOUNT.BILLING_CANCEL_FAILED],
    ['UNKNOWN_CODE', 'Eliminazione non riuscita. Riprova.'],
  ])('stays signed in and explains a refused delete (%s)', async (code, message) => {
    vi.mocked(authClient.deleteUser).mockResolvedValue({
      data: null,
      error: { code, status: 409, statusText: 'Conflict' },
    });
    const dialog = await openDialog();
    await type(field(dialog, 'Scrivi ELIMINA per confermare'), 'ELIMINA');
    await click(control(dialog, 'Elimina account'));
    expect(replace).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith(message);
  });
});
