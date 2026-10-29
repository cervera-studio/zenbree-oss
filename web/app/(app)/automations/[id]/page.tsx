import { Pencil } from 'lucide-react';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { getTranslations } from 'next-intl/server';
import { AppPage } from '@/components/app/page';
import { AutomationControls } from '@/components/automations/automation-controls';
import { AutomationFacts } from '@/components/automations/automation-facts';
import { AutomationRuns } from '@/components/automations/automation-runs';
import { SignalSetup } from '@/components/automations/signal-setup';
import { WebhookSetup } from '@/components/automations/webhook-setup';
import { Button } from '@/components/ui/button';
import { getSession } from '@/lib/auth-token';
import { getAutomation, getRuns } from '@/lib/automations/api';
import { canManage } from '@/lib/automations/run-view';
import { isUuid } from '@/lib/config/uuid';
import { isOrgAdmin } from '@/lib/org';

export default async function AutomationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!isUuid(id)) notFound();
  const [t, te, automation, runs, session, admin] = await Promise.all([
    getTranslations('automations'),
    getTranslations('automationEditor'),
    getAutomation(id),
    getRuns(id),
    getSession(),
    isOrgAdmin(),
  ]);
  if (!automation.success) {
    if (automation.error.status === 404) notFound();
    return (
      <p role="alert" className="text-negative p-6 text-ui">
        {t('loadError')}
      </p>
    );
  }
  const a = automation.data;
  const viewer = { id: session?.user.id ?? '', isOrgAdmin: admin };

  return (
    <AppPage
      crumbs={[{ label: t('title'), href: '/automations' }, { label: a.name }]}
      crumbsLabel={t('breadcrumb')}
      topbarActions={
        canManage(viewer, a) && (
          <Button asChild variant="outline" className="phone:h-11">
            <Link href={`/automations/${a.id}/edit`}>
              <Pencil aria-hidden />
              {te('edit')}
            </Link>
          </Button>
        )
      }
      title={a.name}
      meta={<AutomationFacts automation={a} />}
      actions={<AutomationControls automation={a} viewer={viewer} />}
    >
      {!canManage(viewer, a) && <p className="text-muted text-small">{t('head.readOnly')}</p>}
      {a.triggerKind === 'webhook' && <WebhookSetup automation={a} manage={canManage(viewer, a)} />}
      {a.signal && <SignalSetup signal={a.signal} />}
      {runs.success ? (
        <AutomationRuns automation={a} runs={runs.data.runs} />
      ) : (
        <p role="alert" className="text-negative text-ui">
          {t('loadError')}
        </p>
      )}
    </AppPage>
  );
}
