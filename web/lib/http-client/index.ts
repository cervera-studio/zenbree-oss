/**
 * httpClient — VNet fetch wrapper for server-side use only.
 *
 * NEVER import this in client components.
 * All requests go to the FastAPI backend on the internal network (API_BASE_URL).
 * Returns Result<T, AppError> — never throws.
 *
 * Automatically transforms:
 *   - Response keys: snake_case → camelCase (backend → frontend DTO), unless
 *     `camel: false` keeps the wire's keys (a body keyed by column key)
 *   - Request body keys: camelCase → snake_case (frontend DTO → backend)
 *
 * P3-HARD-02: `import 'server-only'` makes the build FAIL if a client
 * component accidentally imports this module — instead of leaking
 * captured env (e.g. JWTs passed via the `token` option) into the
 * browser bundle.
 */
import 'server-only';

import type { StandardSchemaV1 } from '@standard-schema/spec';
import { cookies } from 'next/headers';
import { contractError, toAppError, unreachable } from '@/lib/api/error';
import { getInternalApiBaseUrl } from '@/lib/config/env';
import { logger } from '@/lib/logger';
import { PROJECT_COOKIE } from '@/lib/projects/cookie';
import { fail, type Result, succeed } from '@/lib/types';
import { toCamelCase, toSnakeCase } from './case-transform';
import { buildQueryUrl, type QueryParamValue } from './query-params';

const INTERNAL_API_BASE_URL = getInternalApiBaseUrl();
const DEFAULT_TIMEOUT_MS = 10_000;

interface RequestOptions<T = unknown> extends Omit<RequestInit, 'method' | 'body'> {
  params?: Record<string, QueryParamValue>;
  token?: string;
  /** The project (#307) to answer for, over the active one in the cookie: a page about one campaign reads its
   * project's senders, whichever client is active. */
  project?: string;
  timeoutMs?: number;
  camel?: boolean;
  /**
   * Checks the response against a contract before it is handed back.
   *
   * Without one, `T` is a CAST: the body arrives as whatever `API` sent,
   * and TypeScript is describing a hope. That matters here more than in a
   * single-repo app because `API` deploys on its own cadence (merge →
   * staging, prod = a manual tag), so the two sides are never swapped
   * atomically. A renamed field or a new `null` fails nowhere: it becomes
   * `undefined` halfway through a component, and what you see is an empty cell
   * or a crash three renders later — never an error at the boundary it
   * crossed. `MeAccount.capabilities` carries that warning in its own
   * docblock, twice; this is the mechanism that answers it.
   *
   * Any Standard Schema works (zod 4 implements it natively via `~standard`),
   * so this package stays free of a validation library and the caller brings
   * the one its app already has.
   *
   * Validation runs AFTER the snake→camel transform, because the contract
   * describes the frontend DTO, not the wire (with `camel: false`, the wire). Use loose/passthrough objects:
   * a stripping schema would silently delete every field it does not declare,
   * which turns "check the shape" into "narrow the payload".
   */
  schema?: StandardSchemaV1<unknown, T>;
}

/** A API URL on the internal host, with the same path checks `httpClient` applies. */
function buildInternalUrl(path: string, params?: Record<string, QueryParamValue>): Result<string> {
  return buildQueryUrl(path, INTERNAL_API_BASE_URL, params);
}

function buildHeaders(token?: string, contentType?: string): Record<string, string> {
  const headers: Record<string, string> = {};
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  if (contentType) {
    headers['Content-Type'] = contentType;
  }
  return headers;
}

/**
 * Renders the failed contract as one line: `items.0.amount: expected number`.
 *
 * The field path is the whole value of the message — "response did not match
 * the schema" names a file, not a cause, and the reader's next question is
 * always which field. Capped at three because a renamed container fails every
 * row under it, and a hundred identical issues bury the one that differs.
 */
function contractMessage(issues: ReadonlyArray<StandardSchemaV1.Issue>): string {
  const named = issues.slice(0, 3).map((issue) => {
    const path = (issue.path ?? [])
      .map((segment) => (typeof segment === 'object' ? String(segment.key) : String(segment)))
      .join('.');
    return path ? `${path}: ${issue.message}` : issue.message;
  });
  const rest = issues.length - named.length;
  return named.join('; ') + (rest > 0 ? ` (+${rest} more)` : '');
}

function encodeBody(
  token: string | undefined,
  body: unknown,
): { headers: Record<string, string>; body: RequestInit['body'] } {
  // FormData: browser/fetch fills in `multipart/form-data; boundary=...`
  // automatically; setting Content-Type ourselves clobbers the boundary
  // and corrupts the wire payload. Skip toSnakeCase too — the caller
  // already shaped the multipart parts (the BE's parse helper reads
  // `data` JSON + `files`).
  const isMultipart = typeof FormData !== 'undefined' && body instanceof FormData;
  return {
    headers: buildHeaders(token, isMultipart ? undefined : body ? 'application/json' : undefined),
    body: isMultipart ? (body as FormData) : body ? JSON.stringify(toSnakeCase(body)) : undefined,
  };
}

/** The active project (#307) from the request's cookie; none outside a request (build, cron). */
async function projectHeader(project?: string): Promise<Record<string, string>> {
  try {
    const id = project ?? (await cookies()).get(PROJECT_COOKIE)?.value;
    return id ? { 'X-Project-Id': id } : {};
  } catch {
    return {};
  }
}

/**
 * Every failed call, one `user_facing_failure` line, then the Result unchanged.
 *
 * Every Server Action reaches `API` through here, so this is the one
 * place a failure the UI is about to render can be counted with its code —
 * including the ones the backend never sees (network, timeout, a body that
 * breaks its contract). `path` is the route without its query string: ids,
 * never PII. Same event name as `API`'s boundaries, so one query reads
 * both sides.
 */
async function request<T>(
  method: string,
  path: string,
  options?: RequestOptions<T> & { body?: unknown },
): Promise<Result<T>> {
  const result = await send<T>(method, path, options);
  if (!result.success) {
    const { code, status, message } = result.error;
    (status >= 500 ? logger.warn : logger.info)('user_facing_failure', {
      surface: 'http_client',
      code,
      status,
      method,
      path: redactPath(path),
      detail: message.slice(0, 200),
    });
  }
  return result;
}

// An unsubscribe token is the recipient's credential (#172): opaque, but still kept out of logs.
function redactPath(path: string): string {
  return path.replace(/^\/v1\/unsubscribe\/[^/?]+/, '/v1/unsubscribe/<token>');
}

async function send<T>(
  method: string,
  path: string,
  options?: RequestOptions<T> & { body?: unknown },
): Promise<Result<T>> {
  const { params, token, body, timeoutMs, schema, project, camel, ...fetchOptions } = options ?? {};

  // A dot-segment path never reaches `fetch`.
  const url = buildInternalUrl(path, params);
  if (!url.success) return url;

  const encoded = encodeBody(token, body);
  const response = await fetch(url.data, {
    method,
    ...encoded,
    headers: { ...encoded.headers, ...(await projectHeader(project)) },
    signal: AbortSignal.timeout(timeoutMs ?? DEFAULT_TIMEOUT_MS),
    ...fetchOptions,
  }).catch(() => null);

  // Network error (DNS, timeout, connection refused)
  if (!response) return fail(unreachable());

  const responseBody = await response.json().catch(() => null);
  if (!response.ok) return fail(toAppError(response.status, responseBody));

  const data = camel === false ? (responseBody as T) : toCamelCase<T>(responseBody);
  if (!schema) return succeed(data);

  // `await` unconditionally: the spec lets a schema validate asynchronously,
  // and awaiting a plain value costs a microtask.
  const validation = await schema['~standard'].validate(data);
  if (validation.issues) {
    // 502, not 500: the request was served, and what came back is the
    // problem. A caller that already branches on `INTERNAL.*` keeps working —
    // this needs no new channel, which is the point of returning it as a
    // Result. `data` carries the issues so Sentry shows the field, not just
    // the route.
    return fail(
      contractError(
        `${method} ${path} returned a body that does not match its contract — ${contractMessage(validation.issues)}`,
        { issues: validation.issues },
      ),
    );
  }
  return succeed(validation.value);
}

export const httpClient = {
  get<T>(path: string, options?: RequestOptions<T>): Promise<Result<T>> {
    return request<T>('GET', path, options);
  },

  post<T>(path: string, body?: unknown, options?: RequestOptions<T>): Promise<Result<T>> {
    return request<T>('POST', path, { ...options, body });
  },

  patch<T>(path: string, body?: unknown, options?: RequestOptions<T>): Promise<Result<T>> {
    return request<T>('PATCH', path, { ...options, body });
  },

  /**
   * Full replacement. Distinct from `patch` because several `API`
   * resources are PUT-only and treat the verb as meaning: the caller is
   * sending the whole document and owns the result. `/api/voice-profile` is
   * the first — a PUT there is by definition a human edit, so the backend
   * flags `user_edited` and later agent regenerations refuse to clobber it.
   * Routing that through PATCH would 405.
   */
  put<T>(path: string, body?: unknown, options?: RequestOptions<T>): Promise<Result<T>> {
    return request<T>('PUT', path, { ...options, body });
  },

  /** `body` is for the few routes that take one (`DELETE .../rows {rowKeys}`). */
  delete<T>(path: string, options?: RequestOptions<T> & { body?: unknown }): Promise<Result<T>> {
    return request<T>('DELETE', path, options);
  },
};
