import { describe, expect, it } from 'vite-plus/test';
import { minutesLeft } from './impersonation-minutes';

const now = new Date('2026-10-14T10:00:00Z');

describe('minutesLeft', () => {
  it.each([
    ['a partial minute counts as one', '2026-10-14T10:00:01Z', 1],
    ['40 minutes and a half rounds up', '2026-10-14T10:40:30Z', 41],
    ['the exact expiry is 0', '2026-10-14T10:00:00Z', 0],
    ['an expiry in the past stays 0', '2026-10-14T09:30:00Z', 0],
  ])('%s', (_, expiresAt, minutes) => {
    expect(minutesLeft(expiresAt, now)).toBe(minutes);
  });
});
