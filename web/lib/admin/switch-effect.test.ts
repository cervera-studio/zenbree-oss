import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vite-plus/test';
import { switchEffect } from './switch-effect';

describe('switchEffect', () => {
  it.each([
    ['vendor_disabled:apollo', 'lookup'],
    ['vendor_disabled:pdl', 'lookup'],
    ['vendor_disabled:google', 'mailbox'],
    ['vendor_disabled:microsoft', 'mailbox'],
    ['vendor_disabled:unipile', 'unipile'],
    ['channel_disabled:email', 'email'],
    ['channel_disabled:linkedin', 'linkedin'],
    ['vendor_disabled:hubspot', 'crm'],
    ['vendor_disabled:instantly', 'instantly'],
    ['vendor_disabled:newvendor', 'other'],
    ['vendor_disabled:constructor', 'other'],
    ['channel_disabled:__proto__', 'other'],
  ])('%s holds back: %s', (key, effect) => {
    expect(switchEffect(key)).toBe(effect);
  });

  it('knows every switch the settings page describes', () => {
    const page = readFileSync(join(process.cwd(), 'app/admin/settings/page.tsx'), 'utf8');
    const keys = [...page.matchAll(/'((?:vendor|channel)_disabled)_(\w+)'/g)].map(
      (m) => `${m[1]}:${m[2]}`,
    );
    expect(keys.length).toBeGreaterThan(10);
    expect(keys.filter((k) => switchEffect(k) === 'other')).toEqual([]);
  });
});
