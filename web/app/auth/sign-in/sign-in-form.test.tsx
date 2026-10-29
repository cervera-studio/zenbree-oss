// @vitest-environment happy-dom
import { act } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { authClient } from '@/lib/auth-client';
import { LEGAL } from '@/lib/legal';
import { setStored } from '@/lib/storage/storage';
import { click, control, field, render } from '../testing';
import { encodeBan, LAST_EMAIL_KEY } from './params';
import { SignInForm } from './sign-in-form';

const push = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));
vi.mock('@/lib/auth-client', () => ({
  authClient: { signIn: { email: vi.fn(), social: vi.fn() } },
}));

const signIn = vi.mocked(authClient.signIn.email);

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

const alert = (c: HTMLElement) => c.querySelector('[role="alert"]')?.textContent ?? null;
const status = (c: HTMLElement) => c.querySelector('[role="status"]')?.textContent ?? null;

async function submit(c: HTMLElement, email: string) {
  field(c, 'Email').value = email;
  field(c, 'Password').value = 'correct horse';
  await click(control(c, 'Accedi'));
}

describe('SignInForm notices', () => {
  it('shows the expired notice as a status and prefills the last email', async () => {
    localStorage.setItem(LAST_EMAIL_KEY, 'me@example.com');
    const c = await render(<SignInForm expired callbackUrl="/" canSignUp />);
    expect(status(c)).toContain('Sessione scaduta');
    expect(alert(c)).toBeNull();
    expect(field(c, 'Email').value).toBe('me@example.com');
  });

  it('keeps text typed before the stored email arrives', async () => {
    const c = await render(<SignInForm expired callbackUrl="/" canSignUp />);
    const input = field(c, 'Email');
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(
        input,
        'typed@example.com',
      );
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => setStored(LAST_EMAIL_KEY, 'me@example.com', 'local'));
    expect(field(c, 'Email').value).toBe('typed@example.com');
  });

  it('does not prefill outside the expired notice', async () => {
    localStorage.setItem(LAST_EMAIL_KEY, 'me@example.com');
    const c = await render(<SignInForm expired={false} callbackUrl="/" canSignUp />);
    expect(status(c)).toBeNull();
    expect(field(c, 'Email').value).toBe('');
  });

  it('shows only the generic ban for an OAuth ban, with the support address', async () => {
    const c = await render(
      <SignInForm expired callbackUrl="/" canSignUp oauthError="BANNED_USER" />,
    );
    expect(alert(c)).toContain('Account sospeso');
    expect(alert(c)).toContain('La sospensione non ha una data di fine.');
    expect(alert(c)).not.toContain('Motivo');
    expect(status(c)).toBeNull();
    const mail = control(c, `Scrivi a ${LEGAL.contactEmail}`);
    expect(mail.getAttribute('href')).toBe(`mailto:${LEGAL.contactEmail}`);
  });

  it('shows the ban the server found instead of the expired notice (#762)', async () => {
    const ban = { reason: 'Spam', expires: '2026-12-01T09:00:00.000Z' };
    const c = await render(<SignInForm expired ban={ban} callbackUrl="/" canSignUp />);
    expect(alert(c)).toContain('Account sospeso');
    expect(alert(c)).toContain('Motivo: Spam');
    expect(alert(c)).toContain('1 dicembre 2026');
    expect(status(c)).toBeNull();
  });

  it('shows the generic error and re-enables submit when the request fails', async () => {
    signIn.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    const c = await render(<SignInForm expired={false} callbackUrl="/" canSignUp />);
    await submit(c, 'me@example.com');
    expect(alert(c)).toBe('Qualcosa è andato storto. Riprova.');
    expect(control(c, 'Accedi').hasAttribute('disabled')).toBe(false);
    expect(push).not.toHaveBeenCalled();
  });

  it('hides the sign-up link when sign-up is closed', async () => {
    const c = await render(<SignInForm expired={false} callbackUrl="/" canSignUp={false} />);
    expect(c.querySelector('a[href^="/auth/sign-up"]')).toBeNull();
  });

  it('shows any other OAuth error as the generic error', async () => {
    const c = await render(
      <SignInForm expired={false} callbackUrl="/" canSignUp oauthError="nope" />,
    );
    expect(alert(c)).toBe('Qualcosa è andato storto. Riprova.');
  });

  it('tells a Microsoft sign-up with an unverified address to sign up with email first', async () => {
    const c = await render(
      <SignInForm
        expired={false}
        callbackUrl="/"
        canSignUp
        oauthError="MICROSOFT_EMAIL_UNVERIFIED"
      />,
    );
    expect(alert(c)).toMatch(/^Questo indirizzo Microsoft non è verificato\./);
  });

  it('shows reason and expiry when the email sign-in is banned', async () => {
    signIn.mockResolvedValue({
      data: null,
      error: {
        code: 'BANNED_USER',
        message: encodeBan({ banReason: 'Spam', banExpires: new Date('2026-12-01T09:00:00Z') }),
        status: 403,
        statusText: 'Forbidden',
      },
    });
    const c = await render(<SignInForm expired callbackUrl="/" canSignUp />);
    await submit(c, 'me@example.com');
    expect(alert(c)).toContain('Motivo: Spam');
    expect(alert(c)).toContain('1 dicembre 2026');
    expect(status(c)).toBeNull();
    expect(push).not.toHaveBeenCalled();
  });
});

describe('SignInForm', () => {
  it('signs in, remembers the email and returns to the callback', async () => {
    signIn.mockResolvedValue({ data: null, error: null });
    const c = await render(<SignInForm expired={false} callbackUrl="/lists/1" canSignUp />);
    await submit(c, 'me@example.com');
    expect(signIn).toHaveBeenCalledWith({
      email: 'me@example.com',
      password: 'correct horse',
      callbackURL: '/lists/1',
    });
    expect(localStorage.getItem(LAST_EMAIL_KEY)).toBe('me@example.com');
    expect(push).toHaveBeenCalledWith('/lists/1');
  });

  it('sends a failed social sign-in back to the sign-in page', async () => {
    const c = await render(<SignInForm expired={false} callbackUrl="/lists/1" canSignUp />);
    await click(control(c, 'Continua con Google'));
    expect(authClient.signIn.social).toHaveBeenCalledWith({
      provider: 'google',
      callbackURL: '/lists/1',
      errorCallbackURL: '/auth/sign-in',
    });
  });
});
