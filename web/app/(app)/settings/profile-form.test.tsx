// @vitest-environment happy-dom
import { act } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { click, control, field, render } from '@/app/auth/testing';
import { authClient } from '@/lib/auth-client';
import { ProfileForm } from './profile-form';

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock('next-themes', () => ({ useTheme: () => ({ theme: 'system', setTheme: vi.fn() }) }));
vi.mock('./actions', () => ({ setCompanyNameAction: vi.fn(), setLocaleAction: vi.fn() }));
vi.mock('@/lib/auth-client', () => ({
  authClient: {
    updateUser: vi.fn(),
    listAccounts: vi.fn(() => Promise.resolve({ data: [] })),
  },
}));

beforeEach(() => vi.clearAllMocks());

async function type(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  await act(async () => {
    setter?.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

const hasCompany = (c: HTMLElement) =>
  [...c.querySelectorAll('label')].some((l) => l.textContent === 'Azienda');

describe('ProfileForm', () => {
  it('saves the name twice without showing «Azienda» to a user with no profile', async () => {
    vi.mocked(authClient.updateUser).mockResolvedValue({ data: { status: true }, error: null });
    const c = await render(
      <ProfileForm
        name="Ada"
        email="ada@example.com"
        image={null}
        emailVerified
        company={null}
        locale="it"
        timeZone={null}
      />,
    );
    expect(hasCompany(c)).toBe(false);
    for (const name of ['Ada L', 'Ada Lovelace']) {
      await type(field(c, 'Nome'), name);
      await click(control(c, 'Salva'));
      expect(authClient.updateUser).toHaveBeenLastCalledWith({ name });
      expect(hasCompany(c)).toBe(false);
    }
    expect(authClient.updateUser).toHaveBeenCalledTimes(2);
  });

  it('keeps «Azienda» for a user with a profile after a save', async () => {
    vi.mocked(authClient.updateUser).mockResolvedValue({ data: { status: true }, error: null });
    const c = await render(
      <ProfileForm
        name="Ada"
        email="ada@example.com"
        image={null}
        emailVerified
        company="Acme"
        locale="it"
        timeZone={null}
      />,
    );
    await type(field(c, 'Nome'), 'Ada L');
    await click(control(c, 'Salva'));
    expect(field(c, 'Azienda').value).toBe('Acme');
  });
});
