// #368 against a real database: the signup wallet takes `signup_bonus_credits`, or 25.
import { randomUUID } from 'node:crypto';
import type { Pool } from 'pg';
import { afterAll, beforeAll, describe, expect, it } from 'vite-plus/test';
import { grantSignupCredits } from './signup-credits';
import { migratedDatabase } from './test-database';

const DB_URL = process.env.TEST_DATABASE_URL;

describe.skipIf(!DB_URL)('signup credits (#368)', () => {
  let pool: Pool;
  let drop: (() => Promise<void>) | undefined;

  async function signUp() {
    const id = randomUUID();
    await pool.query(
      'INSERT INTO "user" (id, name, email, "emailVerified", "createdAt", "updatedAt") VALUES ($1, $2, $3, false, now(), now())',
      [id, 'ada', `${id}@x.test`],
    );
    const { rows } = await pool.query('SELECT default_organization($1) AS org', [id]);
    await grantSignupCredits(pool, rows[0].org, id);
    const credits = await pool.query(
      'SELECT credits_remaining FROM user_credits WHERE user_id = $1',
      [id],
    );
    return credits.rows[0]?.credits_remaining;
  }

  const setBonus = (value: string | null) =>
    value === null
      ? pool.query("DELETE FROM settings WHERE key = 'signup_bonus_credits'")
      : pool.query(
          "INSERT INTO settings (key, value) VALUES ('signup_bonus_credits', $1::jsonb) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
          [value],
        );

  beforeAll(async () => {
    ({ pool, drop } = await migratedDatabase(DB_URL ?? ''));
  });

  afterAll(async () => {
    await drop?.();
  });

  it('grants 25 when the setting is missing', async () => {
    await setBonus(null);
    expect(await signUp()).toBe(25);
  });

  it('grants the configured amount', async () => {
    await setBonus('120');
    expect(await signUp()).toBe(120);
    await setBonus('0');
    expect(await signUp()).toBe(0);
  });

  it.each(['"120"', '-5', '1.5', 'true', '3000000000'])(
    'grants 25 when the setting is %s',
    async (v) => {
      await setBonus(v);
      expect(await signUp()).toBe(25);
    },
  );
});
