// #981 against a real database: the per-address cap on auth emails.
import { randomUUID } from 'node:crypto';
import type { Pool } from 'pg';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vite-plus/test';
import { AUTH_EMAIL_LIMIT, authEmailSender } from './auth-email-throttle';
import { migratedDatabase } from './test-database';

const sent = vi.hoisted(() => [] as string[]);
vi.mock('./email-layout', () => ({
  sendAuthEmail: async (_template: string, to: string) => {
    sent.push(to);
  },
}));

const DB_URL = process.env.TEST_DATABASE_URL;

describe.skipIf(!DB_URL)('auth emails per recipient address (#981)', () => {
  let pool: Pool;
  let drop: (() => Promise<void>) | undefined;
  let send: ReturnType<typeof authEmailSender>;
  const mail = (to: string) => send('reset-password', to, 'Ada', 'https://x.test/r', 'en');

  beforeAll(async () => {
    ({ pool, drop } = await migratedDatabase(DB_URL ?? ''));
    send = authEmailSender(pool);
  });

  afterAll(async () => {
    await drop?.();
  });

  it('sends up to the cap to one address, then skips silently whatever the case', async () => {
    const to = `ada-${randomUUID().slice(0, 8)}@x.test`;
    sent.length = 0;
    for (let i = 0; i < AUTH_EMAIL_LIMIT + 3; i++) await mail(i % 2 ? to.toUpperCase() : to);
    expect(sent).toHaveLength(AUTH_EMAIL_LIMIT);
  });

  it('counts concurrent sends once each', async () => {
    const to = `bo-${randomUUID().slice(0, 8)}@x.test`;
    sent.length = 0;
    await Promise.all(Array.from({ length: 12 }, () => mail(to)));
    expect(sent).toHaveLength(AUTH_EMAIL_LIMIT);
  });

  it('keeps another address and a later day free', async () => {
    const to = `cy-${randomUUID().slice(0, 8)}@x.test`;
    for (let i = 0; i < AUTH_EMAIL_LIMIT; i++) await mail(to);
    sent.length = 0;
    await mail(`di-${randomUUID().slice(0, 8)}@x.test`);
    expect(sent).toHaveLength(1);
    await pool.query(`UPDATE auth_email_sends SET sent_at = now() - interval '25 hours'`);
    await mail(to);
    expect(sent).toHaveLength(2);
  });
});
