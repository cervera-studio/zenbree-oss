import type { CaptureResult } from 'posthog-js';
import { describe, expect, it } from 'vite-plus/test';
import { isUnsubscribePage, scrubEvent } from './analytics';

const event = (properties: CaptureResult['properties']): CaptureResult => ({
  uuid: '1',
  event: '$pageview',
  properties,
});
const APP = 'https://app.zenbree.com';
const props = (properties: CaptureResult['properties']) =>
  scrubEvent(event({ $current_url: `${APP}/lists`, ...properties }))?.properties;

describe('scrubEvent', () => {
  it('drops events on the unsubscribe page, by $current_url or $pathname', () => {
    expect(scrubEvent(event({ $current_url: 'https://u.zenbree.com/x/abc' }))).toBeNull();
    expect(scrubEvent(event({ $current_url: `${APP}/u/abc` }))).toBeNull();
    expect(scrubEvent(event({ $pathname: '/u/S1' }))).toBeNull();
  });

  it('removes the reset token and keeps absolute URLs absolute', () => {
    expect(props({ $referrer: `${APP}/auth/reset-password?token=S0&next=%2F` })?.$referrer).toBe(
      `${APP}/auth/reset-password?next=%2F`,
    );
  });

  it('redacts unsubscribe paths, relative and absolute', () => {
    const p = props({
      $referrer: '/u/S2',
      $initial_referrer: 'https://u.zenbree.com/u/S3',
      $prev_pageview_url: `${APP}/x/S3b`,
    });
    expect(p?.$referrer).toBe('/u/redacted');
    expect(p?.$initial_referrer).toBe('https://u.zenbree.com/u/redacted');
    expect(p?.$prev_pageview_url).toBe(`${APP}/x/redacted`);
  });

  it('removes the token from a relative path and keeps it relative', () => {
    expect(props({ $prev_pageview_pathname: '/auth/reset-password?token=S4' })).toMatchObject({
      $prev_pageview_pathname: '/auth/reset-password',
    });
  });

  it('removes a token whose key is percent-encoded', () => {
    expect(props({ $referrer: `${APP}/auth/reset-password?%74oken=S9` })?.$referrer).toBe(
      `${APP}/auth/reset-password`,
    );
  });

  it('redacts tokens inside $elements_chain and $elements', () => {
    const p = props({
      $elements_chain: 'a:text="Reset"attr__href="/auth/reset-password?token=S5"href="/x/S5b"',
      $elements: [{ tag_name: 'a', attr__href: `${APP}/auth/reset-password?token=S6` }],
    });
    expect(JSON.stringify(p)).not.toMatch(/S5|S5b|S6/);
  });

  it('walks nested objects, arrays, $set and $set_once', () => {
    const scrubbed = scrubEvent({
      ...event({
        $current_url: `${APP}/lists`,
        nested: { deep: [{ url: `${APP}/auth/reset-password?token=S7` }] },
        $snapshot_data: [{ type: 4, data: { href: `${APP}/auth/reset-password?token=S7b` } }],
      }),
      $set: { $initial_referrer: `${APP}/u/S8` },
      $set_once: { $initial_current_url: `${APP}/auth/reset-password?token=S8b` },
    });
    expect(JSON.stringify(scrubbed)).not.toMatch(/S7|S8/);
  });

  it('keeps other events and plain text as they are', () => {
    const plain = event({
      $current_url: `${APP}/users/1?tokens=2`,
      $pathname: '/users/1',
      note: 'see /u/abc and token=xyz',
      count: 2,
      at: null,
    });
    expect(scrubEvent(plain)).toEqual(plain);
  });
});

describe('isUnsubscribePage', () => {
  it('matches only the unsubscribe paths', () => {
    expect(isUnsubscribePage('/u/abc')).toBe(true);
    expect(isUnsubscribePage('/x/abc')).toBe(true);
    expect(isUnsubscribePage('/users')).toBe(false);
  });
});
