import { randomUUID } from 'node:crypto';
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { Pool } from 'pg';

const MIGRATIONS = path.resolve(__dirname, '../../api/db');

/**
 * A new database on the test server with api/db applied, for one test file, and the function that drops it.
 * A database, not a schema in the shared one: Better Auth reads the table list of the whole database, and a schema
 * that another run built or dropped at that moment failed it. The zbtest_ prefix is the API tests' own.
 */
export async function migratedDatabase(
  url: string,
): Promise<{ pool: Pool; drop: () => Promise<void> }> {
  const name = `zbtest_${randomUUID().replaceAll('-', '').slice(0, 12)}`;
  const admin = new Pool({ connectionString: url, max: 1 });
  await admin.query(`CREATE DATABASE ${name}`);
  const target = new URL(url);
  target.pathname = `/${name}`;
  const pool = new Pool({ connectionString: target.toString() });
  // ponytail: applies api/db by hand, not through app.core.migrate: the web job has no Python toolchain. It skips
  // the runner's checks (checksums, file names, schema_migrations); if the runner ever does more than run each file
  // in order, this database drifts from the real one. Then shell out to the runner.
  const drop = async () => {
    await pool.end();
    // Not WITH (FORCE): pool.end() resolves before its sockets close, and a forced drop kills them with an error
    // nobody listens for. A plain drop waits up to 5 s for them to go.
    await admin.query(`DROP DATABASE ${name}`);
    await admin.end();
  };
  try {
    for (const file of readdirSync(MIGRATIONS)
      .filter((f) => f.endsWith('.sql'))
      .sort()) {
      await pool.query(readFileSync(path.join(MIGRATIONS, file), 'utf8'));
    }
  } catch (error) {
    await drop();
    throw error;
  }
  return { pool, drop };
}
