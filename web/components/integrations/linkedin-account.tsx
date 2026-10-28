import { useTranslations } from 'next-intl';
import { SettingsItem } from '@/components/settings/settings-card';
import { Avatar } from '@/components/ui/avatar';
import { Badge } from '@/components/ui/badge';
import { LinkedInDisconnect } from './linkedin-disconnect';

export function LinkedInAccount({
  label,
  premium,
  canDisconnect,
}: {
  label?: string | null;
  premium: boolean;
  /** #307: the account is the project's; another member uses it but cannot disconnect it. */
  canDisconnect: boolean;
}) {
  const t = useTranslations('integrations.linkedin');
  const name = label ?? t('name');
  return (
    <SettingsItem>
      <Avatar user={{ name, email: '' }} size="lg" tone="accent" />
      <span className="text-ui-strong flex min-w-0 grow items-center gap-2">
        <span className="truncate">{name}</span>
        {premium && <Badge tone="accent">{t('premium')}</Badge>}
      </span>
      {canDisconnect && <LinkedInDisconnect />}
    </SettingsItem>
  );
}
