import Link from 'next/link';
import { getTranslations } from 'next-intl/server';
import { Button } from '@/components/ui/button';
import type { BusinessProfile } from '@/lib/onboarding/schema';
import { SettingsSection } from './settings-section';

const FIELDS = [
  'summary',
  'personas',
  'painPoints',
  'solution',
  'buyingTriggers',
  'markets',
  'exclusions',
  'proof',
] as const;

/** The business profile onboarding saved for the active client, read-only. */
export async function ClientProfile({ profile }: { profile: BusinessProfile | null }) {
  const t = await getTranslations('clientSettings.profile');
  const s = await getTranslations('onboarding.profile.sections');
  const rows = FIELDS.flatMap((key) => {
    const value = profile?.[key];
    const items = (Array.isArray(value) ? value : [value]).filter((v): v is string => !!v);
    return items.length ? [{ key, items }] : [];
  });
  return (
    <SettingsSection
      title={profile?.companyName ? t('titleNamed', { name: profile.companyName }) : t('title')}
      description={t('description')}
      action={
        <Button asChild size="sm" variant={profile ? 'outline' : 'primary'}>
          <Link href="/onboarding?redo=1">{t(profile ? 'redo' : 'create')}</Link>
        </Button>
      }
    >
      {rows.length === 0 ? (
        <p className="text-ui text-muted">{t('empty')}</p>
      ) : (
        <dl className="text-ui flex flex-col gap-3">
          {rows.map(({ key, items }) => (
            <div key={key}>
              <dt className="text-ui-strong">{s(key)}</dt>
              <dd className="text-muted">
                {/* one entry per line: profile entries are sentences, so a joined line reads "x.; y" */}
                {items.length === 1 ? (
                  items[0]
                ) : (
                  <ul className="list-disc pl-4">
                    {items.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                )}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </SettingsSection>
  );
}
