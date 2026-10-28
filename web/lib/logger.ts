// The only place that touches `console` (Biome `noConsole`): a level gate in front of it, so a
// level can be switched in production without a deploy. Python's level names, as the API
// (`api/app/core/logging.py`): one `LOG_LEVEL` sets both, and a query on `level` matches both.
// No level above `error`: an error is never muted.
// - Server: `LOG_LEVEL` (runtime env, default `info`). One JSON line per call, as the API writes;
//   Vector ships the container's stdout to Better Stack.
// - Browser: `warning` in production, `debug` in development; `localStorage['zb-log-level']`
//   overrides it (set it in the devtools console to reproduce an issue). The error SDK keeps
//   every console line it sees as a breadcrumb on the next error report, so muting warnings here
//   would strip that context.

const LEVELS = ['debug', 'info', 'warning', 'error'] as const;
type Level = (typeof LEVELS)[number];
type LogFields = Record<string, unknown>;

export interface Logger {
  debug(event: string, fields?: LogFields): void;
  info(event: string, fields?: LogFields): void;
  warn(event: string, fields?: LogFields): void;
  error(event: string, fields?: LogFields): void;
}

const CONSOLE = { debug: 'debug', info: 'info', warning: 'warn', error: 'error' } as const;
const BROWSER_LEVEL_KEY = 'zb-log-level';
// Keys whose value never reaches a log line, as the API's `_SECRET_KEYS`. Compared lowercase
// without `_`, so `apiKey` and `api_key` both match; exact names, so `inputTokens` stays.
const SECRET_KEYS = new Set([
  'token',
  'accesstoken',
  'refreshtoken',
  'idtoken',
  'authorization',
  'password',
  'secret',
  'clientsecret',
  'cookie',
  'apikey',
  'email',
  'phone',
]);

function asLevel(value: string | null | undefined): Level | null {
  return LEVELS.find((level) => level === value) ?? null;
}

function browserLevel(): Level | null {
  try {
    return asLevel(window.localStorage.getItem(BROWSER_LEVEL_KEY));
  } catch {
    return null; // storage blocked (private window, sandboxed frame)
  }
}

let warnedUnknownLevel = false;

function serverLevel(): Level {
  const configured = process.env.LOG_LEVEL;
  const level = asLevel(configured);
  if (level) return level;
  // The API refuses to start on a typo; the web cannot, so it says so once and logs at `info`.
  if (configured && !warnedUnknownLevel) {
    warnedUnknownLevel = true;
    console.warn(JSON.stringify({ level: 'warning', event: 'logger.unknown_level', configured }));
  }
  return 'info';
}

// Read on every call: a level set in the console applies without a reload.
function threshold(): Level {
  if (typeof window === 'undefined') return serverLevel();
  return browserLevel() ?? (process.env.NODE_ENV === 'production' ? 'warning' : 'debug');
}

// An Error becomes a plain object here, so its `cause` (often an object) is redacted too, on the
// server and in the browser console the error SDK reads breadcrumbs from.
function redact(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redact);
  if (value instanceof Error) {
    const { name, message, stack, cause } = value;
    return { name, message, stack, cause: redact(cause) };
  }
  if (!value || Object.getPrototypeOf(value) !== Object.prototype) return value;
  return Object.fromEntries(
    Object.entries(value).map(([key, v]) => [
      key,
      SECRET_KEYS.has(key.toLowerCase().replaceAll('_', '')) ? '[redacted]' : redact(v),
    ]),
  );
}

// JSON.stringify throws on a BigInt.
function serialisable(_key: string, value: unknown): unknown {
  return typeof value === 'bigint' ? value.toString() : value;
}

function emit(level: Level, event: string, fields: LogFields): void {
  const safe = redact(fields);
  if (typeof window !== 'undefined') {
    console[CONSOLE[level]](event, safe);
    return;
  }
  // The logger's own keys last: a field named `level` or `event` cannot hide them.
  const line = { ...(safe as LogFields), level, event, timestamp: new Date().toISOString() };
  console[CONSOLE[level]](JSON.stringify(line, serialisable));
}

// A log call never throws into its caller, which is often already handling an error: a field it
// cannot serialise (a cycle) costs the fields, not the line.
function write(level: Level, event: string, fields: LogFields = {}): void {
  if (LEVELS.indexOf(level) < LEVELS.indexOf(threshold())) return;
  try {
    emit(level, event, fields);
  } catch {
    emit(level, event, { loggerError: 'fields not serialisable' });
  }
}

export const logger: Logger = {
  debug: (event, fields) => write('debug', event, fields),
  info: (event, fields) => write('info', event, fields),
  warn: (event, fields) => write('warning', event, fields),
  error: (event, fields) => write('error', event, fields),
};
