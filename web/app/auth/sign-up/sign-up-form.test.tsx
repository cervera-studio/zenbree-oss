// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { authClient } from '@/lib/auth-client';
import { MESSAGES } from '@/messages';
import { click, control, field, render } from '../testing';
import { VERIFY_EMAIL_KEY } from '../verify/verify-email';
import { SignUpForm } from './sign-up-form';

const push = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));
vi.mock('@/lib/auth-client', () => ({
  authClient: { signUp: { email: vi.fn() }, signIn: { social: vi.fn() } },
}));

const signUp = vi.mocked(authClient.signUp.email);

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
});

async function submit(c: HTMLElement) {
  field(c, 'Nome').value = 'Ada Lovelace';
  field(c, 'Email di lavoro').value = 'ada@example.com';
  field(c, 'Password').value = 'correct horse';
  await click(control(c, 'Registrati'));
}

describe('SignUpForm', () => {
  it('sends a new account to the verify page and remembers the address for this tab', async () => {
    signUp.mockResolvedValue({ data: null, error: null });
    const c = await render(<SignUpForm />);
    await submit(c);
    expect(signUp).toHaveBeenCalledWith({
      email: 'ada@example.com',
      password: 'correct horse',
      name: 'Ada Lovelace',
      callbackURL: '/',
    });
    expect(push).toHaveBeenCalledWith('/auth/verify');
    expect(sessionStorage.getItem(VERIFY_EMAIL_KEY)).toBe('ada@example.com');
  });

  it('stays on the form and shows the error when sign-up fails', async () => {
    signUp.mockResolvedValue({
      data: null,
      error: {
        code: 'USER_ALREADY_EXISTS',
        message: 'User already exists',
        status: 422,
        statusText: 'Unprocessable',
      },
    });
    const c = await render(<SignUpForm />);
    await submit(c);
    expect(c.querySelector('[role="alert"]')?.textContent).toBe(
      MESSAGES.it.errors.AUTH_CLIENT.USER_ALREADY_EXISTS,
    );
    expect(push).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(VERIFY_EMAIL_KEY)).toBeNull();
  });

  it('describes the password with its length rule', async () => {
    const c = await render(<SignUpForm />);
    const hint = c.querySelector(`#${field(c, 'Password').getAttribute('aria-describedby')}`);
    expect(hint?.textContent).toBe('Almeno 8 caratteri.');
  });

  it('links the terms and the privacy policy in the consent line', async () => {
    const c = await render(<SignUpForm />);
    expect(control(c, 'Termini').getAttribute('href')).toBe('/terms');
    expect(control(c, 'Informativa privacy').getAttribute('href')).toBe('/privacy');
    expect(c.textContent).toContain("e l'Informativa privacy.");
  });

  it('names the compact social buttons by their action', async () => {
    const c = await render(<SignUpForm />);
    await click(control(c, 'Registrati con Microsoft'));
    expect(authClient.signIn.social).toHaveBeenCalledWith({
      provider: 'microsoft',
      callbackURL: '/',
      errorCallbackURL: '/auth/sign-in',
    });
  });
});

describe('SignUpForm from an invitation (#323)', () => {
  it('lands the verification link, the social sign-up and the verify page back on the invitation', async () => {
    signUp.mockResolvedValue({ data: null, error: null });
    const c = await render(<SignUpForm callbackUrl="/invite/abc" />);
    expect(control(c, 'Accedi').getAttribute('href')).toBe(
      '/auth/sign-in?callbackUrl=%2Finvite%2Fabc',
    );
    await submit(c);
    expect(signUp).toHaveBeenCalledWith(expect.objectContaining({ callbackURL: '/invite/abc' }));
    expect(push).toHaveBeenCalledWith('/auth/verify?callbackUrl=%2Finvite%2Fabc');
    await click(control(c, 'Registrati con Google'));
    expect(authClient.signIn.social).toHaveBeenCalledWith(
      expect.objectContaining({ provider: 'google', callbackURL: '/invite/abc' }),
    );
  });
});
