/**
 * Browser → Python API. The client-side sibling of `httpClient` (server-only):
 * same Result + snake→camel + optional zod contract, plus the Better Auth
 * bearer with one refresh-and-retry on 401.
 */
import type { StandardSchemaV1 } from '@standard-schema/spec';
import { getAuthToken } from '@/lib/chat/auth';
import { getPublicApiBaseUrl } from '@/lib/config/env';
import { toCamelCase, toSnakeCase } from '@/lib/http-client/case-transform';
import { readProjectCookie } from '@/lib/projects/cookie';
import { fail, type Result, succeed } from '@/lib/types';
import { contractError, responseError, unreachable } from './error';

export async function authFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const url = `${getPublicApiBaseUrl()}${path}`;
  const project = readProjectCookie();
  const send = (token: string | null) =>
    fetch(url, {
      ...init,
      headers: {
        ...init.headers,
        ...(token && { Authorization: `Bearer ${token}` }),
        ...(project && { 'X-Project-Id': project }),
      },
    });
  const res = await send(await getAuthToken());
  return res.status === 401 ? send(await getAuthToken(true)) : res;
}

interface ApiOptions<T> {
  method?: string;
  body?: unknown;
  /** Sent as is under its own content type (a CSV upload), instead of `body` as JSON. */
  file?: { body: Blob; type: string };
  schema?: StandardSchemaV1<unknown, T>;
  /** false = keep the wire's snake_case (List cells are keyed by column key). */
  camel?: boolean;
}

export async function api<T>(path: string, opts: ApiOptions<T> = {}): Promise<Result<T>> {
  let res: Response;
  try {
    res = await authFetch(path, {
      method: opts.method ?? 'GET',
      ...(opts.body !== undefined && {
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(toSnakeCase(opts.body)),
      }),
      ...(opts.file && { headers: { 'Content-Type': opts.file.type }, body: opts.file.body }),
    });
  } catch {
    return fail(unreachable());
  }
  if (!res.ok) return fail(await responseError(res));
  const body = await res.json().catch(() => null);
  const data = opts.camel === false ? (body as T) : toCamelCase<T>(body);
  if (!opts.schema) return succeed(data);
  const v = await opts.schema['~standard'].validate(data);
  if (v.issues) {
    const first = v.issues[0];
    return fail(contractError(`${path}: ${first?.message ?? 'bad shape'}`));
  }
  return succeed(v.value);
}
