/**
 * Pure query-string serialization for `httpClient`.
 *
 * Lives in a separate module from `index.ts` because `index.ts` carries
 * `import 'server-only'` — that side-effect import throws on `require`
 * in any test environment, so unit tests cannot reach the serializer
 * if it ships in the same module. Splitting it out lets vitest exercise
 * the array-param contract directly, while `index.ts` re-exports
 * `buildQueryUrl` + `QueryParamValue` so existing import paths keep
 * working.
 *
 * It is also the one place a backend URL is built, so it refuses unsafe
 * path segments for every caller, `fetch`-direct ones included.
 */

import { fail, type Result, succeed, typedError } from '@/lib/types';

/**
 * Query-string parameter value. Scalars are written verbatim via
 * `URLSearchParams.append`. Array values produce repeated keys
 * (`foo=a&foo=b`) — this is the wire shape FastAPI uses for `list[str]`
 * query params (e.g. `ateco_codes=25&ateco_codes=28`). `undefined` and
 * `null` entries (or array elements) are dropped, so callers can splat
 * optional UI state without pre-filtering.
 */
export type QueryParamValue =
  | string
  | number
  | boolean
  | ReadonlyArray<string | number | boolean>
  | undefined
  | null;

function appendParam(url: URL, key: string, value: string | number | boolean): void {
  url.searchParams.append(key, String(value));
}

const PATH_SYNTAX = /[/\\?#]/;

/**
 * Characters `new URL` removes or trims before parsing, per the WHATWG URL
 * Standard's basic URL parser: it strips leading and trailing C0 controls and
 * spaces (U+0000–U+0020) and removes every ASCII tab or newline. Left in, they
 * let a segment pass the check below and still parse as `..` (`.\t.`,
 * `.. ` at the end). All of U+0000–U+0020 is refused anywhere, query included:
 * `seg` and the query encoders never emit one raw.
 */
function hasCharStrippedByUrl(input: string): boolean {
  for (let i = 0; i < input.length; i++) {
    if (input.charCodeAt(i) <= 0x20) return true;
  }
  return false;
}

/**
 * API routes on the percent-DECODED path: `w1%2Fbusiness-profile` is
 * matched as `w1/business-profile`. So a segment is judged by what it decodes
 * to, once — empty (Starlette redirects `/sessions/` to the collection),
 * `.`/`..` (also spelled `%2e`), or a `/ \ ? #` inside would reach another
 * route with the caller's token. A double-encoded value (`%252F`) decodes to
 * text and passes. A malformed escape fails: nothing we build produces one.
 */
function isUnsafeSegment(segment: string): boolean {
  let decoded: string;
  try {
    decoded = decodeURIComponent(segment);
  } catch {
    return true;
  }
  return decoded === '' || decoded === '.' || decoded === '..' || PATH_SYNTAX.test(decoded);
}

/** An absolute path whose every segment stays one segment of one route. */
function isSafePath(path: string): boolean {
  if (!path.startsWith('/') || hasCharStrippedByUrl(path)) return false;
  const pathPart = path.split(/[?#]/, 1)[0] ?? '';
  return !pathPart.slice(1).split(/[/\\]/).some(isUnsafeSegment);
}

/**
 * Resolves `path` against `baseUrl` and appends `params`. Fails with
 * `VALIDATION.INVALID_PATH` when a path segment would not stay one segment of
 * one route once API decodes it, or when the path would leave the base
 * URL's origin (`//host/…`, a scheme).
 */
export function buildQueryUrl(
  path: string,
  baseUrl: string,
  params?: Record<string, QueryParamValue>,
): Result<string> {
  const invalid = fail<string>(
    typedError('VALIDATION.INVALID_PATH', 'Path is not a single route of the backend', 422),
  );
  if (!isSafePath(path)) return invalid;
  const url = new URL(path, baseUrl);
  if (url.origin !== new URL(baseUrl).origin) return invalid;
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === null) continue;
      if (Array.isArray(value)) {
        for (const item of value) {
          if (item === undefined || item === null) continue;
          appendParam(url, key, item);
        }
        continue;
      }
      // TS narrowing: after Array.isArray fails, `value` is still typed
      // as the original union — including the readonly-array variant —
      // because `Array.isArray` doesn't refine `ReadonlyArray<T>`. Cast
      // to the scalar leaves since the runtime check above is total.
      appendParam(url, key, value as string | number | boolean);
    }
  }
  return succeed(url.toString());
}
