import { UsersRound } from 'lucide-react';
import { headers } from 'next/headers';
import { getTranslations } from 'next-intl/server';
import { AuthShell } from '@/app/auth/auth-shell';
import { auth } from '@/lib/auth';
import { getSession } from '@/lib/auth-token';
import { InviteActions } from './invite-actions';

// Outside the (app) group: an invited user who just signed up has not been through onboarding yet.
// The proxy sends a signed-out visitor to sign-in and back here.
export default async function InvitePage({ params }: { params: Promise<{ id: string }> }) {
  const [{ id }, t, session] = await Promise.all([
    params,
    getTranslations('team.invitation'),
    getSession(),
  ]);
  // Not found, expired, already answered, or addressed to someone else: Better Auth refuses them all.
  const invitation = await auth.api
    .getInvitation({ query: { id }, headers: await headers() })
    .catch(() => null);
  const icon = (
    <span className="flex size-14 items-center justify-center rounded-xl bg-accent-tint text-accent-ink">
      <UsersRound aria-hidden className="size-6.5" strokeWidth={1.75} />
    </span>
  );

  if (!invitation) {
    return (
      <AuthShell
        layout="message"
        title={t('invalidTitle')}
        lede={t('invalidBody', { email: session?.user.email ?? '' })}
        icon={icon}
      >
        <InviteActions id={id} valid={false} />
      </AuthShell>
    );
  }
  return (
    <AuthShell
      layout="message"
      title={t('title', { name: invitation.organizationName })}
      lede={t('body', {
        inviter: invitation.inviterEmail,
        name: invitation.organizationName,
        role: invitation.role === 'admin' ? 'admin' : 'member',
      })}
      icon={icon}
    >
      <InviteActions id={id} valid />
    </AuthShell>
  );
}
