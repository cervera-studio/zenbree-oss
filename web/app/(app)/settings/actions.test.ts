import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';

const set = vi.fn();
vi.mock('next/headers', () => ({
  cookies: async () => ({ set }),
  headers: async () => new Headers(),
}));

const updateUser = vi.fn();
vi.mock('@/lib/auth', () => ({ auth: { api: { updateUser: (a: unknown) => updateUser(a) } } }));

const getSession = vi.fn();
vi.mock('@/lib/auth-token', () => ({ getSession: () => getSession() }));

const getOnboarding = vi.fn();
const saveProfile = vi.fn();
vi.mock('@/lib/onboarding/server', () => ({
  getOnboarding: () => getOnboarding(),
  saveProfile: (profile: unknown) => saveProfile(profile),
}));

const { setLocaleAction, setCompanyNameAction } = await import('./actions');

describe('setLocaleAction', () => {
  beforeEach(() => {
    set.mockClear();
    updateUser.mockReset();
    getSession.mockReset().mockResolvedValue({ user: { id: 'u1' }, session: {} });
  });

  it('saves the choice on the signed-in user', async () => {
    await setLocaleAction('en');
    expect(updateUser).toHaveBeenCalledWith(expect.objectContaining({ body: { locale: 'en' } }));
  });

  it('sets the cookie but saves nothing for a visitor or an impersonating admin', async () => {
    getSession.mockResolvedValue(null);
    await setLocaleAction('en');
    getSession.mockResolvedValue({ user: { id: 'u1' }, session: { impersonatedBy: 'admin' } });
    await setLocaleAction('en');
    expect(updateUser).not.toHaveBeenCalled();
    expect(set).toHaveBeenCalledTimes(2);
  });

  it('does not throw when the save fails', async () => {
    updateUser.mockRejectedValue(new Error('down'));
    await expect(setLocaleAction('en')).resolves.toBeUndefined();
    expect(set).toHaveBeenCalledOnce();
  });

  it('stores a supported locale in the cookie request.ts reads', async () => {
    await setLocaleAction('en');
    expect(set).toHaveBeenCalledWith('NEXT_LOCALE', 'en', expect.objectContaining({ path: '/' }));
  });

  it('ignores anything else', async () => {
    await setLocaleAction('de');
    await setLocaleAction('en; Path=/admin');
    expect(set).not.toHaveBeenCalled();
  });
});

describe('setCompanyNameAction', () => {
  const profile = { companyName: 'Acme', summary: 'Anvils', personas: ['Ops'], extra: 1 };

  beforeEach(() => {
    getSession.mockReset().mockResolvedValue({ user: { id: 'u1' } });
    getOnboarding.mockReset().mockResolvedValue({ success: true, data: { profile } });
    saveProfile.mockReset().mockResolvedValue({ success: true });
  });

  it('saves the whole profile with only the company name changed', async () => {
    expect(await setCompanyNameAction('  Globex ')).toBe(true);
    expect(saveProfile).toHaveBeenCalledWith({ ...profile, companyName: 'Globex' });
  });

  it('writes nothing without a session, a profile or a usable name', async () => {
    expect(await setCompanyNameAction('   ')).toBe(false);
    getSession.mockResolvedValue(null);
    expect(await setCompanyNameAction('Globex')).toBe(false);
    getSession.mockResolvedValue({ user: { id: 'u1' } });
    getOnboarding.mockResolvedValue({ success: true, data: { profile: null } });
    expect(await setCompanyNameAction('Globex')).toBe(false);
    expect(saveProfile).not.toHaveBeenCalled();
  });
});
