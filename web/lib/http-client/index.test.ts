import { afterEach, describe, expect, it, vi } from 'vite-plus/test';
import { logger } from '@/lib/logger';

vi.mock('server-only', () => ({}));
process.env.API_BASE_URL = 'http://api.test';
const { httpClient } = await import('./index');

describe('httpClient failure log', () => {
  afterEach(() => vi.restoreAllMocks());

  it('keeps an unsubscribe token out of the log (#172)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: { code: 'UNSUBSCRIBE.INVALID_LINK', message: 'x' } }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const info = vi.spyOn(logger, 'info').mockImplementation(() => undefined);
    await httpClient.get('/v1/unsubscribe/gAAAAABsecret/state');
    expect(info).toHaveBeenCalledWith(
      'user_facing_failure',
      expect.objectContaining({ path: '/v1/unsubscribe/<token>/state' }),
    );
    expect(JSON.stringify(info.mock.calls)).not.toContain('gAAAAABsecret');
  });
});

describe('httpClient response keys', () => {
  afterEach(() => vi.restoreAllMocks());

  const answer = () =>
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ next_cursor: null, cells: { full_name: 'Marco' } }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

  it('turns the wire keys to camelCase by default', async () => {
    answer();
    expect(await httpClient.get('/v1/x')).toEqual({
      success: true,
      data: { nextCursor: null, cells: { fullName: 'Marco' } },
    });
  });

  it('keeps the wire keys with camel: false, so a cell keyed by column key survives', async () => {
    answer();
    expect(await httpClient.get('/v1/x', { camel: false })).toEqual({
      success: true,
      data: { next_cursor: null, cells: { full_name: 'Marco' } },
    });
  });
});
