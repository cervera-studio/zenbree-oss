import { authClient } from '@/lib/auth-client';

/**
 * Bearer for the Python API from the browser: the Better Auth JWT (`/api/auth/token`,
 * EdDSA, verified against JWKS by FastAPI). Replaces the earlier implementation's Supabase access token.
 *
 * ponytail: no cache on purpose. A module-level token outlives soft navigations, so after
 * sign-out/sign-in or admin impersonation the tab kept calling the API as the previous
 * user. One same-origin fetch per API call is cheap; cache per session id if it shows up.
 * `refresh` stays for the callers' 401-retry path.
 */
export async function getAuthToken(_refresh = false): Promise<string | null> {
  const { data } = await authClient.token();
  return data?.token ?? null;
}
