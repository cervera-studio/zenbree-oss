import { getLocale, getTranslations } from 'next-intl/server';
import { getSession } from '@/lib/auth-token';
import { getRequestOnboarding } from '@/lib/onboarding/server';
import { isOrgAdmin } from '@/lib/org';
import { ProfileForm } from './profile-form';

export default async function ProfilePage() {
  const [t, locale, session, onboarding, admin] = await Promise.all([
    getTranslations('settings'),
    getLocale(),
    getSession(),
    getRequestOnboarding(),
    isOrgAdmin(),
  ]);

  return (
    <div className="@container flex flex-col gap-4">
      <h2 className="text-feature desktop:sr-only">{t('title')}</h2>
      {/* Two columns once the content area is wide enough; the layout's one breakpoint is the viewport's. */}
      <ProfileForm
        name={session?.user.name ?? ''}
        email={session?.user.email ?? ''}
        image={session?.user.image ?? null}
        emailVerified={session?.user.emailVerified ?? false}
        company={onboarding.success ? (onboarding.data.profile?.companyName ?? null) : null}
        locale={locale}
        timeZone={session?.user.timeZone ?? null}
        admin={admin}
      />
    </div>
  );
}
