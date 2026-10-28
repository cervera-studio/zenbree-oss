/**
 * The one reader of an API error. The server client, the browser client and the chat and
 * onboarding streams all turn a failed response into an `AppErrorJSON` here, so a code reaches
 * the UI the same way whichever path carried it. The user reads the text `useErrorText()` picks
 * for `code`; `message` is the API's English, for logs only.
 */
import { toCamelCase } from '@/lib/http-client/case-transform';
import type { AppErrorJSON } from '@/lib/types';

type Fields = Record<string, unknown>;

const isObject = (value: unknown): value is Fields =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

function appError(code: string, message: string, status: number, data?: Fields): AppErrorJSON {
  return { name: 'AppError', code, message, status, ...(data && { data }) };
}

/** A body that carries no code still gets one from its status, so the UI can choose a text. */
function statusCode(status: number): string {
  if (status === 413) return 'REQUEST.TOO_LARGE';
  if (status === 422) return 'REQUEST.INVALID_INPUT';
  if (status === 502 || status === 503 || status === 504) return 'INTERNAL.SERVICE_UNAVAILABLE';
  if (status >= 500) return 'INTERNAL.UNEXPECTED';
  return `REQUEST.HTTP_${status}`;
}

/**
 * Accepts `{detail: {code, message, ...extra}}` (the API's shape; `extra` becomes `data`),
 * `{detail: {error: {code}}}` (`unwrap_or_raise`), `{error: {code, data?}}`, FastAPI's own
 * `{detail: [{loc, msg, type}]}` and `{detail: "text"}`, and anything else, JSON or not.
 */
export function toAppError(status: number, body: unknown): AppErrorJSON {
  const detail = isObject(body) ? body.detail : undefined;
  const structured = [
    isObject(body) ? body.error : undefined,
    isObject(detail) ? detail.error : undefined,
    detail,
  ].find((e): e is Fields => isObject(e) && typeof e.code === 'string');
  if (structured) {
    const { code, message, data, ...extra } = structured;
    const payload = isObject(data) ? data : extra;
    return appError(
      String(code),
      typeof message === 'string' ? message : `HTTP ${status}`,
      status,
      Object.keys(payload).length > 0 ? toCamelCase<Fields>(payload) : undefined,
    );
  }
  if (Array.isArray(detail)) {
    return appError('REQUEST.INVALID_INPUT', 'Invalid input', status, { fields: detail });
  }
  return appError(
    statusCode(status),
    typeof detail === 'string' ? detail : `HTTP ${status}`,
    status,
  );
}

/** Reads the failed response's body, JSON or not. */
export async function responseError(res: Response): Promise<AppErrorJSON> {
  return toAppError(res.status, await res.json().catch(() => null));
}

/** The request never got an answer (offline, DNS, timeout, refused). */
export const unreachable = (): AppErrorJSON =>
  appError('INTERNAL.SERVICE_UNAVAILABLE', 'API unreachable', 503);

/** A 2xx body that breaks the contract the caller asked to check. */
export const contractError = (detail: string, data?: Fields): AppErrorJSON =>
  appError('INTERNAL.RESPONSE_CONTRACT', detail, 502, data);
