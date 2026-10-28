import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';

const { setActiveOrganization, del } = vi.hoisted(() => ({
  setActiveOrganization: vi.fn(),
  del: vi.fn(),
}));
vi.mock('@/lib/auth', () => ({
  auth: {
    api: {
      listOrganizations: async () => [{ id: 'a', name: 'A' }],
      getSession: async () => ({ session: { activeOrganizationId: 'b' } }),
      setActiveOrganization,
    },
  },
}));
vi.mock('next/headers', () => ({
  headers: async () => new Headers(),
  cookies: async () => ({ delete: del }),
}));
vi.mock('next/navigation', () => ({
  redirect: (to: string) => {
    throw new Error(`redirect:${to}`);
  },
}));
vi.mock('server-only', () => ({}));

import { GET } from './route';

const url = 'http://x/switch?org=a&next=%2F%3Fs%3D1';

describe('GET /switch', () => {
  beforeEach(() => vi.clearAllMocks());

  it('switches to an organization of the user, then opens next', async () => {
    await expect(GET(new Request(url))).rejects.toThrow('redirect:/?s=1');
    expect(setActiveOrganization).toHaveBeenCalledOnce();
    expect(del).toHaveBeenCalledOnce();
  });

  it.each([
    ['next-router-prefetch', '1'],
    ['purpose', 'prefetch'],
    ['sec-purpose', 'prefetch'],
  ])('changes nothing on a prefetch (%s)', async (name, value) => {
    const headers = new Headers({ [name]: value });
    await expect(GET(new Request(url, { headers }))).rejects.toThrow('redirect:');
    expect(setActiveOrganization).not.toHaveBeenCalled();
    expect(del).not.toHaveBeenCalled();
  });

  it('ignores an organization the user is not in', async () => {
    await expect(GET(new Request(url.replace('org=a', 'org=z')))).rejects.toThrow('redirect:');
    expect(setActiveOrganization).not.toHaveBeenCalled();
  });
});
