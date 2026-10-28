/**
 * Result type — explicit success/failure, no try/catch.
 * Use with ts-pattern for exhaustive matching.
 *
 * `success` discriminant, `data`/`error` fields.
 *
 * Result.error is a plain object (not an `Error` subclass) because
 * React's RSC serializer collapses any `instanceof Error` value to a
 * generic stripped Error on the client in production builds — see
 * `react-server-dom-*-server.production.js`:
 *   `if (value instanceof Error) return "$Z";`
 * — and the client deserializer replaces it via `resolveErrorProd()`
 * with an Error whose message is "An error occurred in the Server
 * Components render…". So shipping an `AppError` instance across a
 * server-action return value destroys code/message/status on the
 * wire. Keep `AppError` for throw paths and internal plumbing; ship
 * the JSON shape across the RSC boundary.
 */

import type { ErrorCode } from './errors';

export class AppError extends Error {
  constructor(
    public readonly code: string,
    public readonly message: string,
    public readonly status: number = 400,
    public readonly data?: Record<string, unknown>,
  ) {
    super(message);
    this.name = 'AppError';
  }
}

export interface AppErrorJSON {
  readonly name: 'AppError';
  readonly code: string;
  readonly message: string;
  readonly status: number;
  readonly data?: Record<string, unknown>;
}

export type Result<T> = { success: true; data: T } | { success: false; error: AppErrorJSON };

export function succeed<T>(data: T): Result<T> {
  return { success: true, data };
}

export function fail<T = never>(error: AppError | AppErrorJSON): Result<T> {
  const json: AppErrorJSON =
    error instanceof AppError
      ? {
          name: 'AppError',
          code: error.code,
          message: error.message,
          status: error.status,
          data: error.data,
        }
      : error;
  return { success: false, error: json };
}

/**
 * Type-safe constructor for known error codes.
 * Prefer this over `new AppError(...)` when using codes from the error taxonomy.
 */
export function typedError(code: ErrorCode, message: string, status?: number): AppError {
  return new AppError(code, message, status);
}
