/**
 * Validated environment variables — fail fast with clear errors.
 * Import this instead of using process.env directly with non-null assertions.
 */

import { logger } from '@/lib/logger';

let warnedApiBaseUrlMissing = false;

/**
 * API base URL for SERVER-side calls (route handlers, Server Components,
 * server actions, `httpClient`). Internal compose host, never shipped to the
 * browser: no NEXT_PUBLIC_ prefix.
 */
export function getInternalApiBaseUrl(): string {
  const configured = process.env.API_BASE_URL;
  if (configured) return configured;

  // Not a throw: callers run this at module scope. Loud once per process instead,
  // except during `next build` (API_BASE_URL is runtime-only) and in the browser.
  const buildPhase = process.env.NEXT_PHASE === 'phase-production-build';
  if (typeof window === 'undefined' && !buildPhase && isProduction() && !warnedApiBaseUrlMissing) {
    warnedApiBaseUrlMissing = true;
    logger.error('config.api_base_url_missing', {
      message: 'API_BASE_URL is unset in production; server-side API calls hit localhost:8000.',
    });
  }
  return 'http://localhost:8000';
}

/**
 * API base URL for BROWSER-side calls. Static `process.env.NEXT_PUBLIC_API_URL`
 * member expression on purpose: Next only inlines that exact shape.
 */
export function getPublicApiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
}

function isProduction(): boolean {
  return process.env.NODE_ENV === 'production';
}
