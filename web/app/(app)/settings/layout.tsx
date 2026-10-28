import type { ReactNode } from 'react';
import { getSettingsMeta } from '@/components/settings/settings-meta';
import { SettingsShell } from '@/components/settings/settings-shell';
import { isOrgAdmin } from '@/lib/org';

export default async function SettingsLayout({ children }: { children: ReactNode }) {
  const [admin, meta] = await Promise.all([isOrgAdmin(), getSettingsMeta()]);
  return (
    <SettingsShell orgAdmin={admin} meta={meta}>
      {children}
    </SettingsShell>
  );
}
