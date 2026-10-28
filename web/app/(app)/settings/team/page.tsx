import { headers } from 'next/headers';
import Link from 'next/link';
import { getTranslations } from 'next-intl/server';
import { SettingsPageHeader } from '@/components/settings/settings-page-header';
import { SettingsSection } from '@/components/settings/settings-section';
import { TeamManager } from '@/components/team/team-manager';
import { auth } from '@/lib/auth';
import { getSession } from '@/lib/auth-token';
import { founderOf } from '@/lib/founder';
import { isOrgAdmin } from '@/lib/org';

export default async function TeamPage() {
  const h = await headers();
  const [t, org, admin, session, received] = await Promise.all([
    getTranslations('team'),
    auth.api.getFullOrganization({ headers: h }).catch(() => null),
    isOrgAdmin(),
    getSession(),
    auth.api.listUserInvitations({ headers: h }).catch(() => []),
  ]);
  const now = Date.now();
  const forMe = received.filter(
    (i) => i.status === 'pending' && new Date(i.expiresAt).getTime() > now,
  );

  return (
    <div className="flex flex-col gap-4">
      <SettingsPageHeader
        title={org ? t('title', { name: org.name }) : t('titleFallback')}
        description={t(admin ? 'descriptionAdmin' : 'descriptionMember')}
      />
      {forMe.length > 0 && (
        <SettingsSection title={t('forMe.title')}>
          <ul className="flex flex-col gap-2">
            {forMe.map((i) => (
              <li key={i.id} className="text-ui flex flex-wrap items-center gap-3">
                <span className="grow">{t('forMe.item', { name: i.organizationName ?? '' })}</span>
                <Link
                  href={`/invite/${i.id}`}
                  className="text-ui-strong text-accent-ink underline-offset-4 hover:underline"
                >
                  {t('forMe.open')}
                </Link>
              </li>
            ))}
          </ul>
        </SettingsSection>
      )}
      {org ? (
        <TeamManager
          admin={admin}
          selfId={session?.user.id ?? ''}
          founderId={founderOf(org.slug)}
          members={org.members.map((m) => ({
            id: m.id,
            userId: m.userId,
            name: m.user.name,
            email: m.user.email,
            role: m.role,
            createdAt: new Date(m.createdAt).toISOString(),
          }))}
          invitations={
            admin
              ? org.invitations
                  .filter((i) => i.status === 'pending')
                  .map((i) => ({
                    id: i.id,
                    email: i.email,
                    role: i.role ?? 'member',
                    expiresAt: new Date(i.expiresAt).toISOString(),
                  }))
              : []
          }
        />
      ) : (
        <p role="alert" className="text-ui text-muted">
          {t('loadError')}
        </p>
      )}
    </div>
  );
}
