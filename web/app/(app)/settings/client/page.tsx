import { getTranslations } from 'next-intl/server';
import { ClientProfile } from '@/components/settings/client-profile';
import { SettingsPageHeader } from '@/components/settings/settings-page-header';
import { SettingsSection } from '@/components/settings/settings-section';
import { getOnboarding } from '@/lib/onboarding/server';
import { isOrgAdmin } from '@/lib/org';
import { getProjects } from '@/lib/projects/server';
import { cn } from '@/lib/utils';
import { CapForm } from './cap-form';

export default async function ClientPage() {
  const [t, admin, res, onboarding] = await Promise.all([
    getTranslations('clientSettings'),
    isOrgAdmin(),
    getProjects(),
    getOnboarding(),
  ]);
  const project = res.success ? res.data.active : undefined;
  if (!project) {
    return (
      <p role="alert" className="text-ui text-muted">
        {t('loadError')}
      </p>
    );
  }
  const cap = project.creditCap && project.creditCapMode ? project.creditCap : null;

  return (
    <div className="flex flex-col gap-4">
      <SettingsPageHeader
        title={t('title', { name: project.name })}
        description={t('description')}
      />
      <SettingsSection
        title={t('cap.title')}
        description={t(admin ? 'cap.descriptionAdmin' : 'cap.descriptionMember')}
      >
        <p className="text-ui">
          {cap
            ? t('cap.spentOf', {
                spent: project.creditsThisMonth,
                cap,
                pct: Math.min(100, Math.round((project.creditsThisMonth / cap) * 100)),
              })
            : t('cap.spent', { spent: project.creditsThisMonth })}
        </p>
        {cap && (
          <div
            role="progressbar"
            aria-label={t('cap.progress')}
            aria-valuemin={0}
            aria-valuemax={cap}
            aria-valuenow={Math.min(project.creditsThisMonth, cap)}
            className="bg-hair h-1 overflow-hidden rounded-xs"
          >
            <div
              className={cn('h-1 rounded-xs', project.overCap ? 'bg-negative' : 'bg-accent')}
              style={{ width: `${Math.min(100, (project.creditsThisMonth / cap) * 100)}%` }}
            />
          </div>
        )}
        {admin ? (
          <CapForm
            key={`${project.id}:${project.creditCap}:${project.creditCapMode}`}
            projectId={project.id}
            amount={project.creditCap ?? null}
            mode={project.creditCapMode ?? null}
          />
        ) : (
          <p className="text-ui text-muted" data-testid="cap-readonly">
            {project.creditCap && project.creditCapMode
              ? t('cap.readOnly', {
                  cap: project.creditCap,
                  mode: project.creditCapMode,
                })
              : t('cap.none')}
          </p>
        )}
      </SettingsSection>
      <ClientProfile profile={onboarding.success ? onboarding.data.profile : null} />
    </div>
  );
}
