// @vitest-environment happy-dom
import { act } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { click, control, field, render } from '@/app/auth/testing';
import { authClient } from '@/lib/auth-client';
import { EmailChange } from './email-change';

vi.mock('@/lib/auth-client', () => ({ authClient: { changeEmail: vi.fn() } }));

beforeEach(() => vi.clearAllMocks());

async function type(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  await act(async () => {
    setter?.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

describe('EmailChange', () => {
  it('requests the change for the new address and says to check it', async () => {
    vi.mocked(authClient.changeEmail).mockResolvedValue({ data: { status: true }, error: null });
    const c = await render(<EmailChange email="ada@example.com" />);
    expect(control(c, 'Cambia email').hasAttribute('disabled')).toBe(true);
    await type(field(c, 'Nuova email'), 'ada@new.test');
    await click(control(c, 'Cambia email'));
    expect(authClient.changeEmail).toHaveBeenCalledWith({
      newEmail: 'ada@new.test',
      callbackURL: '/settings',
    });
    expect(c.textContent).toContain('Controlla il tuo indirizzo attuale');
  });

  it('says so when the request fails', async () => {
    vi.mocked(authClient.changeEmail).mockResolvedValue({
      data: null,
      error: { message: 'no', status: 400, statusText: 'Bad Request' },
    });
    const c = await render(<EmailChange email="ada@example.com" />);
    await type(field(c, 'Nuova email'), 'ada@new.test');
    await click(control(c, 'Cambia email'));
    expect(c.querySelector('[role="alert"]')).not.toBeNull();
  });
});
