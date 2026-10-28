// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { authClient } from '@/lib/auth-client';
import { click, control, field, render } from '../testing';
import { ResetForm } from './reset-form';

const push = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));
vi.mock('@/lib/auth-client', () => ({
  authClient: { requestPasswordReset: vi.fn(), resetPassword: vi.fn(), signIn: { email: vi.fn() } },
}));

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

const ok = { data: { status: true }, error: null };

describe('ResetForm', () => {
  it('asks for the email without a token, then confirms without revealing the account', async () => {
    vi.mocked(authClient.requestPasswordReset).mockResolvedValue(ok);
    const c = await render(<ResetForm token={undefined} />);
    field(c, 'Email').value = 'ada@example.com';
    await click(control(c, 'Invia link'));
    expect(authClient.requestPasswordReset).toHaveBeenCalledWith({
      email: 'ada@example.com',
      redirectTo: '/auth/reset-password',
    });
    expect(c.querySelector('[role="status"]')?.textContent).toBe(
      "Se l'indirizzo esiste, riceverai un'email con il link.",
    );
    expect(c.querySelector('form')).toBeNull();
  });

  it('asks for the new password with a token', async () => {
    vi.mocked(authClient.resetPassword).mockResolvedValue(ok);
    const c = await render(<ResetForm token="tok" />);
    expect(field(c, 'Nuova password').type).toBe('password');
    field(c, 'Nuova password').value = 'a new secret';
    await click(control(c, 'Salva password'));
    expect(authClient.resetPassword).toHaveBeenCalledWith({
      token: 'tok',
      newPassword: 'a new secret',
    });
    expect(c.querySelector('[role="status"]')?.textContent).toBe(
      'Password aggiornata. Ora puoi accedere.',
    );
  });

  it('signs a new account in after its first password and lands on the invitation', async () => {
    localStorage.setItem('zenbree.lastEmail', 'ada@example.com');
    vi.mocked(authClient.resetPassword).mockResolvedValue(ok);
    vi.mocked(authClient.signIn.email).mockResolvedValue({ data: {}, error: null } as never);
    const c = await render(<ResetForm token="tok" isNew callbackUrl="/invite/abc" />);
    field(c, 'Nuova password').value = 'a new secret';
    await click(control(c, 'Salva password'));
    expect(authClient.signIn.email).toHaveBeenCalledWith({
      email: 'ada@example.com',
      password: 'a new secret',
    });
    expect(push).toHaveBeenCalledWith('/invite/abc');
  });

  it('asks a new account to sign in when it cannot, keeping the invitation in the link', async () => {
    vi.mocked(authClient.resetPassword).mockResolvedValue(ok);
    const c = await render(<ResetForm token="tok" isNew callbackUrl="/invite/abc" />);
    field(c, 'Nuova password').value = 'a new secret';
    await click(control(c, 'Salva password'));
    expect(authClient.signIn.email).not.toHaveBeenCalled();
    expect(c.querySelector('[role="status"]')?.textContent).toBe(
      'Password impostata. Accedi per continuare.',
    );
    expect(c.querySelector('a[href="/auth/sign-in?callbackUrl=%2Finvite%2Fabc"]')).toBeTruthy();
  });
});
