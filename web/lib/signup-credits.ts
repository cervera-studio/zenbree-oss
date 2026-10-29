import type { Pool } from 'pg';

// The DB default (api/db credit_repricing migration); the landing promises it, so it stays the grant when the setting is unusable.
const DEFAULT_SIGNUP_CREDITS = 25;
const INT4_MAX = 2_147_483_647;

// The new organization's wallet, opened with the admin setting `signup_bonus_credits` (#368). Like the
// DEFAULT it replaces, the grant writes no ledger row: the ledger starts at the first spend or purchase.
export async function grantSignupCredits(pool: Pool, orgId: string | null, userId: string) {
  const { rows } = await pool.query(
    "SELECT value FROM settings WHERE key = 'signup_bonus_credits'",
  );
  const value: unknown = rows[0]?.value;
  const credits =
    typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= INT4_MAX
      ? value
      : DEFAULT_SIGNUP_CREDITS;
  await pool.query(
    'INSERT INTO user_credits (organization_id, user_id, credits_remaining) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING',
    [orgId, userId, credits],
  );
}
