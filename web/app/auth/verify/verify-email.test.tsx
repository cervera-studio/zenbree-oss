// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { authClient } from '@/lib/auth-client';
import { click, control, render } from '../testing';
import { VERIFY_EMAIL_KEY, VerifyActions, VerifyLede } from './verify-email';

vi.mock('@/lib/auth-client', () => ({
  authClient: { sendVerificationEmail: vi.fn() },
}));

const resend = vi.mocked(authClient.sendVerificationEmail);

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
});

const screen = () => (
  <>
    <VerifyLede />
    <VerifyActions />
  </>
);

describe('verify page', () => {
  it('names the address it wrote to and resends to it', async () => {
    sessionStorage.setItem(VERIFY_EMAIL_KEY, 'ada@example.com');
    resend.mockResolvedValue({ data: { status: true }, error: null });
    const c = await render(screen());
    expect(c.textContent).toContain('Ti abbiamo inviato un link a ada@example.com.');
    await click(control(c, 'inviala di nuovo'));
    expect(resend).toHaveBeenCalledWith({ email: 'ada@example.com', callbackURL: '/' });
    expect(c.querySelector('[role="status"]')?.textContent).toBe(
      'Inviata di nuovo. Controlla la posta.',
    );
  });

  it('reports a failed resend', async () => {
    sessionStorage.setItem(VERIFY_EMAIL_KEY, 'ada@example.com');
    resend.mockResolvedValue({
      data: null,
      error: { message: 'Too many requests', status: 429, statusText: 'Too Many Requests' },
    });
    const c = await render(screen());
    await click(control(c, 'inviala di nuovo'));
    expect(c.querySelector('[role="alert"]')?.textContent).toBe(
      'Qualcosa è andato storto. Riprova.',
    );
  });

  it('falls back to the generic copy without a resend when the address is unknown', async () => {
    const c = await render(screen());
    expect(c.textContent).toContain("Ti abbiamo inviato un link per verificare l'indirizzo.");
    expect(c.querySelector('[role="status"]')?.textContent).toBe(
      'Non è arrivata? Guarda nello spam.',
    );
    expect(() => control(c, 'inviala di nuovo')).toThrow();
  });
});
