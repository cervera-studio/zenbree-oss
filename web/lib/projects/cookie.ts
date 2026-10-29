/**
 * The active project (client, #307) lives in a cookie, not localStorage: server components send it to the API
 * too (`lib/http-client`), as the `X-Project-Id` header the browser client adds (`lib/api/browser.ts`).
 * Written by `setActiveProjectAction`; the billing org switch (`app/(app)/billing/switch`) clears it.
 */
export const PROJECT_COOKIE = 'zb-project';

export function readProjectCookie(): string | null {
  if (typeof document === 'undefined') return null;
  const hit = document.cookie.split('; ').find((c) => c.startsWith(`${PROJECT_COOKIE}=`));
  return hit ? decodeURIComponent(hit.slice(PROJECT_COOKIE.length + 1)) : null;
}
