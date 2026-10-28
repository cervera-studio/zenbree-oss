// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { projectsApi } from './index';
import { loadProjects } from './load';

vi.mock('./index', () => ({ projectsApi: { list: vi.fn() } }));
const list = vi.mocked(projectsApi.list);
const ok = { success: true, data: [] } as never;
const bad = {
  success: false,
  error: { code: 'INTERNAL.SERVICE_UNAVAILABLE', status: 503 },
} as never;

describe('loadProjects', () => {
  let n = 0;
  let user = 'u0';
  beforeEach(() => {
    list.mockReset();
    user = `u${++n}`;
  });

  it('retries once after a failed load', async () => {
    list.mockResolvedValueOnce(bad).mockResolvedValueOnce(ok);
    expect((await loadProjects(user)).success).toBe(true);
    expect(list).toHaveBeenCalledTimes(2);
  });

  it('gives up after the second failure and does not keep it', async () => {
    list.mockResolvedValue(bad);
    expect((await loadProjects(user)).success).toBe(false);
    expect(list).toHaveBeenCalledTimes(2);
    await loadProjects(user);
    expect(list).toHaveBeenCalledTimes(4);
  });

  it('shares one load between the sidebar and the drawer', async () => {
    list.mockResolvedValue(ok);
    await Promise.all([loadProjects(user), loadProjects(user)]);
    await loadProjects(user);
    expect(list).toHaveBeenCalledTimes(1);
    await loadProjects(user, true);
    expect(list).toHaveBeenCalledTimes(2);
  });

  it('refetches when another user signs in on the same tab', async () => {
    list.mockResolvedValue(ok);
    await loadProjects(user);
    await loadProjects(`${user}-other`);
    expect(list).toHaveBeenCalledTimes(2);
  });

  it('does not retry once the page is leaving', async () => {
    list.mockResolvedValue(bad);
    window.dispatchEvent(new Event('pagehide'));
    await loadProjects(user);
    expect(list).toHaveBeenCalledTimes(1);
    window.dispatchEvent(new Event('pageshow'));
    list.mockClear();
    await loadProjects(`${user}-x`);
    expect(list).toHaveBeenCalledTimes(2);
  });
});

describe('loadProjects retry rules', () => {
  it('does not retry a 4xx', async () => {
    list.mockResolvedValue({
      success: false,
      error: { code: 'AUTH.FORBIDDEN', status: 403 },
    } as never);
    await loadProjects('forbidden-user');
    expect(list).toHaveBeenCalledTimes(1);
  });

  it('retries a 401 once, as a failed token fetch looks like one', async () => {
    list
      .mockResolvedValueOnce({
        success: false,
        error: { code: 'AUTH.UNAUTHORIZED', status: 401 },
      } as never)
      .mockResolvedValueOnce(ok);
    expect((await loadProjects('token-user')).success).toBe(true);
    expect(list).toHaveBeenCalledTimes(2);
  });
});
