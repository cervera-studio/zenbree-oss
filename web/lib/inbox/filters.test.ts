import { describe, expect, it, vi } from 'vite-plus/test';
import { clean, href } from './filters';

// The real module is server-only and reads the session.
vi.mock('@/lib/inbox/api', () => ({
  LABELS: ['interested', 'meeting_request', 'not_interested', 'not_now', 'other'],
}));

const UUID = '3f0c2a52-8f5b-4a55-9c1e-6a0d1c3f7b21';

describe('clean', () => {
  it('keeps known values', () => {
    expect(
      clean({
        label: 'not_now',
        channel: 'linkedin',
        unread: '1',
        campaign: UUID,
        sort: 'oldest',
        offset: '50',
      }),
    ).toEqual({
      label: 'not_now',
      channel: 'linkedin',
      unread: '1',
      kind: undefined,
      campaign: UUID,
      sort: 'oldest',
      offset: 50,
    });
  });

  it('keeps the unlabelled view and the opt-out kind', () => {
    expect(clean({ label: 'none' }).label).toBe('none');
    expect(clean({ kind: 'unsubscribe' }).kind).toBe('unsubscribe');
  });

  it('drops what the API would answer 422 to', () => {
    expect(
      clean({
        label: 'spam',
        channel: 'sms',
        unread: '0',
        kind: 'x',
        campaign: 'nope',
        sort: 'random',
        offset: '-4',
      }),
    ).toEqual({
      label: undefined,
      channel: undefined,
      unread: undefined,
      kind: undefined,
      campaign: undefined,
      sort: undefined,
      offset: 0,
    });
  });

  it('leaves the default order out', () => {
    expect(clean({}).sort).toBeUndefined();
    expect(clean({ sort: 'newest' }).sort).toBeUndefined();
  });
});

describe('href', () => {
  it('is the bare inbox with no filter', () => {
    expect(href({})).toBe('/inbox');
    expect(href({ label: undefined, offset: 0 })).toBe('/inbox');
  });

  it('writes each set filter and skips the empty ones', () => {
    expect(href({ label: 'other', channel: 'email', offset: 50 })).toBe(
      '/inbox?label=other&channel=email&offset=50',
    );
  });

  it('keeps the order through a round trip', () => {
    const url = href({ ...clean({ label: 'other', sort: 'oldest' }), m: UUID });
    expect(url).toBe(`/inbox?label=other&sort=oldest&m=${UUID}`);
    const sp = Object.fromEntries(new URL(url, 'http://x').searchParams);
    expect(clean(sp).sort).toBe('oldest');
  });
});
