/** The active organization (#308), read through Better Auth on the server. */
import 'server-only';

import { cookies, headers } from 'next/headers';
import { auth } from './auth';
import { PROJECT_COOKIE } from './projects/cookie';
import { hasRole } from './roles';

/** Whether the caller is an admin of the active organization: billing, team, API keys and webhooks.
 * The API checks the same `member` row on every call; this only decides what the UI shows. */
export async function isOrgAdmin(): Promise<boolean> {
  try {
    const member = await auth.api.getActiveMember({ headers: await headers() });
    return hasRole(member?.role, 'admin');
  } catch {
    return false; // no active organization, or no longer a member of it
  }
}

/** The organizations the caller belongs to, for the switcher. */
export async function listOrgs(): Promise<{ id: string; name: string }[]> {
  try {
    const orgs = await auth.api.listOrganizations({ headers: await headers() });
    return orgs.map(({ id, name }) => ({ id, name }));
  } catch {
    return [];
  }
}

/** A link that is only in view is prefetched by the router: that must not change the session. */
function isPrefetch(request: Request) {
  return (
    request.headers.has('next-router-prefetch') ||
    request.headers.get('purpose') === 'prefetch' ||
    request.headers.get('sec-purpose')?.startsWith('prefetch') === true
  );
}

/** Makes `org` the active organization when the caller belongs to it and it is not already; the old one's
 * client means nothing in the new one, so the project cookie goes. Any other id changes nothing: Better Auth
 * would clear the active organization for it. */
export async function switchOrg(request: Request, org: string | null) {
  if (isPrefetch(request) || !org || !(await listOrgs()).some((o) => o.id === org)) return;
  const session = await auth.api.getSession({ headers: await headers() });
  if (session?.session.activeOrganizationId === org) return;
  await auth.api.setActiveOrganization({ body: { organizationId: org }, headers: await headers() });
  (await cookies()).delete(PROJECT_COOKIE);
}
