import type { Pool } from 'pg';
import { sendAuthEmail } from './email-layout';

// Better Auth limits these emails per IP only, so one address could be mailed from many IPs (#981).
export const AUTH_EMAIL_LIMIT = 5;

/** Count one auth email to ``email``; false past the cap. The advisory lock serialises one address, so two
 * concurrent sends cannot both take the last slot. */
async function claim(pool: Pool, email: string) {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    await client.query('SELECT pg_advisory_xact_lock(hashtext($1))', [`auth-email:${email}`]);
    await client.query(
      `DELETE FROM auth_email_sends WHERE email = $1 AND sent_at < now() - interval '24 hours'`,
      [email],
    );
    const claimed = await client.query(
      `INSERT INTO auth_email_sends (email)
       SELECT $1 WHERE (SELECT count(*) FROM auth_email_sends WHERE email = $1) < $2`,
      [email, AUTH_EMAIL_LIMIT],
    );
    await client.query('COMMIT');
    return claimed.rowCount === 1;
  } catch (e) {
    await client.query('ROLLBACK').catch(() => {});
    throw e;
  } finally {
    client.release();
  }
}

/** `sendAuthEmail` with the per-address cap. Past it the email is skipped without an error: a refusal would
 * tell a caller which addresses have an account. */
export const authEmailSender =
  (pool: Pool) =>
  async (...args: Parameters<typeof sendAuthEmail>) => {
    if (await claim(pool, args[1].toLowerCase())) await sendAuthEmail(...args);
  };
