import type { ReactNode } from 'react';
import { getSettingsMeta } from '@/components/settings/settings-meta';
import { SettingsShell } from '@/components/settings/settings-shell';
import { isOrgAdmin } from '@/lib/org';

// Credits is the last settings tab, but keeps its /billing URL (Stripe return URLs point here).
export default async function BillingLayout({ children }: { children: ReactNode }) {
  const [admin, meta] = await Promise.all([isOrgAdmin(), getSettingsMeta()]);
  return (
    <SettingsShell orgAdmin={admin} meta={meta}>
      {children}
    </SettingsShell>
  );
}
