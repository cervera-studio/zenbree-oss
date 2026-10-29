import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { z } from 'zod';

const token = vi.fn();
vi.mock('@/lib/auth-client', () => ({ authClient: { token: () => token() } }));

import { api } from './browser';

beforeEach(async () => {
  token.mockReset();
  token.mockResolvedValueOnce({ data: { token: 'stale' } });
});

describe('api (browser)', () => {
  it('retries once with a fresh token on 401, then camelCases', async () => {
    token.mockResolvedValueOnce({ data: { token: 'fresh' } });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(Response.json({ credits_remaining: 7 }));
    vi.stubGlobal('fetch', fetchMock);

    const res = await api('/v1/me', { schema: z.object({ creditsRemaining: z.number() }) });

    expect(res).toEqual({ success: true, data: { creditsRemaining: 7 } });
    expect(fetchMock.mock.calls.map(([, i]) => i.headers.Authorization)).toEqual([
      'Bearer stale',
      'Bearer fresh',
    ]);
  });

  it('returns the backend error code and never throws when the API is down', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(
          Response.json({ detail: { code: 'LIST.NOT_FOUND', message: 'nope' } }, { status: 404 }),
        )
        .mockRejectedValueOnce(new TypeError('Failed to fetch')),
    );
    expect(await api('/v1/lists/x')).toMatchObject({
      success: false,
      error: { code: 'LIST.NOT_FOUND', status: 404 },
    });
    expect(await api('/v1/lists/x')).toMatchObject({
      success: false,
      error: { code: 'INTERNAL.SERVICE_UNAVAILABLE', status: 503 },
    });
  });

  it('sends a file as is under its own type, and reads a refusal like any call', async () => {
    const fetchMock = vi.fn(async () =>
      Response.json({ detail: { code: 'LIST.CSV_EMPTY', message: 'empty' } }, { status: 422 }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const file = new Blob(['name\n'], { type: 'text/csv' });
    const res = await api('/v1/lists/upload', {
      method: 'POST',
      file: { body: file, type: 'text/csv' },
    });
    const sent = (fetchMock.mock.calls[0] as unknown[])[1] as RequestInit;
    expect(sent.body).toBe(file);
    expect((sent.headers as Record<string, string>)['Content-Type']).toBe('text/csv');
    expect(res).toMatchObject({ success: false, error: { code: 'LIST.CSV_EMPTY', status: 422 } });
  });

  it('snake_cases acronyms in the body the way the server client does (#445)', async () => {
    const fetchMock = vi.fn(async () => Response.json({}));
    vi.stubGlobal('fetch', fetchMock);
    await api('/v1/x', { method: 'POST', body: { apiURL: 'u', userID: 1 } });
    const sent = (fetchMock.mock.calls[0] as unknown[])[1] as RequestInit;
    expect(JSON.parse(sent.body as string)).toEqual({ api_url: 'u', user_id: 1 });
  });
});

describe('token per call', () => {
  it('asks Better Auth every time, so a session switch never reuses the old user', async () => {
    token.mockReset();
    token
      .mockResolvedValueOnce({ data: { token: 'admin' } })
      .mockResolvedValueOnce({ data: { token: 'impersonated' } });
    const fetchMock = vi
      .fn()
      .mockImplementation(() => Promise.resolve(Response.json({ credits_remaining: 1 })));
    vi.stubGlobal('fetch', fetchMock);
    const schema = z.object({ creditsRemaining: z.number() });
    await api('/v1/me', { schema });
    await api('/v1/me', { schema });
    expect(fetchMock.mock.calls.map(([, i]) => i.headers.Authorization)).toEqual([
      'Bearer admin',
      'Bearer impersonated',
    ]);
  });

  it('names the active project (#307) from its cookie, and none without one', async () => {
    token.mockResolvedValueOnce({ data: { token: 'stale' } });
    const fetchMock = vi.fn(async () => Response.json({}));
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('document', { cookie: 'theme=dark; zb-project=7f0c%2Dp' });
    await api('/v1/inbox');
    vi.stubGlobal('document', { cookie: 'theme=dark' });
    await api('/v1/inbox');
    vi.unstubAllGlobals();
    const sent = fetchMock.mock.calls.map((call) => (call as unknown[])[1] as RequestInit);
    expect(sent.map((i) => (i.headers as Record<string, string>)['X-Project-Id'])).toEqual([
      '7f0c-p',
      undefined,
    ]);
  });
});
