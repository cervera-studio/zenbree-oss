import { headers } from 'next/headers';
import { redirect } from 'next/navigation';
import type { ReactNode } from 'react';
import { expiredPath } from '@/app/auth/sign-in/params';
import { ImpersonationBar } from '@/components/admin/impersonation-bar';
import { AppSidebar } from '@/components/app/app-sidebar';
import { ProjectsViewer } from '@/components/projects/projects-viewer';
import { SavedTimeZoneProvider } from '@/i18n/browser-time-zone';
import { validTimeZone } from '@/i18n/time-zone';
import { auth } from '@/lib/auth';
import { getSession } from '@/lib/auth-token';
import { ChatStreamProvider } from '@/lib/chat/stream-provider';
import { getRequestOnboarding } from '@/lib/onboarding/server';
import { isOrgAdmin, listOrgs } from '@/lib/org';
import { hasRole } from '@/lib/roles';

export default async function AppLayout({ children }: { children: ReactNode }) {
  const session = await getSession();
  // The proxy already turned away requests without a cookie: a cookie with no session
  // means it expired or was revoked (e.g. a password reset on another device).
  if (!session) redirect(expiredPath((await headers()).get('x-zb-path') ?? '/'));
  // New users go through onboarding first. Fail-open: an API error or a slow API
  // (short timeout, this runs on every app page) never locks anyone out.
  const [onboarding, orgs, isOrgAdminNow] = await Promise.all([
    getRequestOnboarding(),
    listOrgs(),
    isOrgAdmin(),
  ]);
  if (onboarding.success && onboarding.data.needsOnboarding) redirect('/onboarding');
  const viewer = {
    id: session.user.id,
    name: session.user.name || session.user.email,
    email: session.user.email,
    image: session.user.image ?? null,
    company: onboarding.success ? (onboarding.data.profile?.companyName ?? null) : null,
    isAdmin: hasRole(session.user.role, 'admin'),
    isOrgAdmin: isOrgAdminNow,
    orgs,
    activeOrgId: session.session.activeOrganizationId ?? null,
  };
  // The impersonated session cannot read other users through the admin API: ask the adapter.
  const impersonator = session.session.impersonatedBy
    ? await (await auth.$context).internalAdapter.findUserById(session.session.impersonatedBy)
    : null;
  return (
    <SavedTimeZoneProvider zone={validTimeZone(session.user.timeZone)}>
      <ChatStreamProvider userId={session.user.id}>
        <div className="flex h-dvh flex-col overflow-hidden">
          {session.session.impersonatedBy && (
            <ImpersonationBar
              name={session.user.email}
              admin={impersonator?.name || impersonator?.email || session.session.impersonatedBy}
              expiresAt={session.session.expiresAt.toISOString()}
            />
          )}
          <div className="flex min-h-0 flex-1 phone:flex-col">
            <AppSidebar viewer={viewer} />
            <div className="min-h-0 min-w-0 flex-1 overflow-y-auto overscroll-contain">
              <ProjectsViewer userId={session.user.id}>{children}</ProjectsViewer>
            </div>
          </div>
        </div>
      </ChatStreamProvider>
    </SavedTimeZoneProvider>
  );
}
