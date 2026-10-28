/**
 * Error codes the FRONTEND itself constructs, via `typedError()`.
 *
 * Read the scope carefully — it is narrower than it looks, and the previous
 * header ("source of truth", pointing at a `docs/error-codes.json` that has
 * never existed in the meta repo) oversold it:
 *
 * - Codes the frontend RAISES locally (guards in use cases, e.g.
 *   `VALIDATION.MISSING_FIELD`) go through `typedError` and ARE constrained by
 *   the `ErrorCode` union below.
 * - Codes the frontend RECEIVES from the backend and pattern-matches on
 *   (`ARTIFACT.*`, `SUPPORT.*`, `INTEGRATIONS.*`, ...) are compared as plain
 *   string literals — `error.code === 'ARTIFACT.VERSION_CONFLICT'` — and are
 *   NOT checked against anything. Many are absent from this file entirely.
 *
 * So the type guards the direction that cannot drift (we write those codes
 * ourselves) and leaves unguarded the one that can: the backend declares its
 * codes at the call sites of `err()` in `app/shared/errors.py`, and renaming
 * one there breaks a frontend branch silently — the comparison simply stops
 * matching and the UI falls through to generic copy.
 *
 * Measured 2026-07-25: 30 of the codes listed here are never emitted by the
 * backend, and whole backend families (AD_MONITOR, RUN, SCHEDULE, WEBHOOKS,
 * WORKSPACE, ...) are unknown here. Adding a code below does NOT make the
 * backend emit it. Grep both sides before inventing a new one.
 */

const AUTH_ERROR_CODES = [
  'AUTH.TOKEN_MISSING',
  'AUTH.TOKEN_EXPIRED',
  'AUTH.TOKEN_INVALID',
  'AUTH.INSUFFICIENT_ROLE',
  'AUTH.USER_NOT_FOUND',
  'AUTH.SESSION_REFRESH_FAILED',
] as const;
type AuthErrorCode = (typeof AUTH_ERROR_CODES)[number];

const BILLING_ERROR_CODES = [
  'BILLING.INSUFFICIENT_CREDITS',
  'BILLING.DEDUCTION_FAILED',
  'BILLING.REFUND_FAILED',
  'BILLING.STRIPE_NOT_CONFIGURED',
  'BILLING.INVALID_PRICE_ID',
  'BILLING.CHECKOUT_FAILED',
  'BILLING.PAYMENT_FAILED',
] as const;
type BillingErrorCode = (typeof BILLING_ERROR_CODES)[number];

const SCRAPE_ERROR_CODES = [
  'SCRAPE.JOB_NOT_FOUND',
  'SCRAPE.JOB_CREATION_FAILED',
  'SCRAPE.QUEUE_UNAVAILABLE',
  'SCRAPE.TASK_TIMEOUT',
  'SCRAPE.TASK_FAILED',
  'SCRAPE.URL_UNREACHABLE',
  'SCRAPE.URL_BLOCKED',
  'SCRAPE.UNSUPPORTED_FILE_TYPE',
  'SCRAPE.FILE_TOO_LARGE',
] as const;
type ScrapeErrorCode = (typeof SCRAPE_ERROR_CODES)[number];

const CHAT_ERROR_CODES = [
  'CHAT.SESSION_NOT_FOUND',
  'CHAT.SESSION_CREATE_FAILED',
  'CHAT.STREAM_INTERRUPTED',
  'CHAT.STREAM_TIMEOUT',
  'CHAT.MAX_TURNS_REACHED',
] as const;
type ChatErrorCode = (typeof CHAT_ERROR_CODES)[number];

const RATE_LIMIT_ERROR_CODES = ['RATE_LIMIT.EXCEEDED', 'RATE_LIMIT.CONCURRENT_LIMIT'] as const;
type RateLimitErrorCode = (typeof RATE_LIMIT_ERROR_CODES)[number];

const LLM_ERROR_CODES = [
  'LLM.PROVIDER_ERROR',
  'LLM.PROVIDER_TIMEOUT',
  'LLM.PROVIDER_RATE_LIMITED',
  'LLM.CONTEXT_TOO_LARGE',
] as const;
type LLMErrorCode = (typeof LLM_ERROR_CODES)[number];

export const VALIDATION_ERROR_CODES = [
  'VALIDATION.MISSING_FIELD',
  'VALIDATION.INVALID_FORMAT',
  'VALIDATION.URL_REQUIRED',
  'VALIDATION.PROMPT_TOO_SHORT',
  // Italian-company discovery (BE PR #706 — F1 unblock). Emitted both by
  // the BE 422 response and by the FE use-case guard when the user hasn't
  // selected any of {ateco_codes, provincia, company_name}. The client
  // pattern-matches on this code regardless of which side rejected.
  'VALIDATION.MISSING_FILTER',
  // `httpClient` refused to build a backend URL: a path segment would not stay
  // one segment of one API route . Frontend-raised.
  'VALIDATION.INVALID_PATH',
] as const;
type ValidationErrorCode = (typeof VALIDATION_ERROR_CODES)[number];

/**
 * Italian-company discovery (BE PR #706). The `OPENAPI_IMPRESE.*` family
 * is a coarse 500-bucket from the upstream openapi.com adapter — the
 * client pivots on `code`, not status, when surfacing copy.
 */
const COMPANIES_IT_ERROR_CODES = [
  'COMPANIES_IT.INVALID_CURSOR',
  'OPENAPI_IMPRESE.RATE_LIMITED',
  'OPENAPI_IMPRESE.AUTH_FAILED',
  'OPENAPI_IMPRESE.PAYMENT_REQUIRED',
  'OPENAPI_IMPRESE.TRANSPORT_ERROR',
  'OPENAPI_IMPRESE.INVALID_RESPONSE',
] as const;
type CompaniesItErrorCode = (typeof COMPANIES_IT_ERROR_CODES)[number];

const INTERNAL_ERROR_CODES = [
  'INTERNAL.UNEXPECTED',
  'INTERNAL.SERVICE_UNAVAILABLE',
  'INTERNAL.DATABASE_ERROR',
  /**
   * The backend answered 200 with a body that does not match the contract the
   * caller asked `httpClient` to check (`schema` on the request options).
   *
   * Frontend-raised, and it stays that way: `API` cannot emit this,
   * because it is this app's verdict on `API`'s answer. Distinct from
   * `INTERNAL.UNEXPECTED` on purpose — that one means the request failed,
   * this one means the two repos have drifted, and they are fixed in
   * different places by different people.
   */
  'INTERNAL.RESPONSE_CONTRACT',
] as const;
type InternalErrorCode = (typeof INTERNAL_ERROR_CODES)[number];

export type ErrorCode =
  | AuthErrorCode
  | BillingErrorCode
  | ScrapeErrorCode
  | ChatErrorCode
  | RateLimitErrorCode
  | LLMErrorCode
  | ValidationErrorCode
  | CompaniesItErrorCode
  | InternalErrorCode;
