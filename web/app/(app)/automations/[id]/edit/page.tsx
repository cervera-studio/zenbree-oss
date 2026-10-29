import Link from 'next/link';
import { notFound } from 'next/navigation';
import { getTranslations } from 'next-intl/server';
import { AppPage } from '@/components/app/page';
import { AutomationEditor } from '@/components/automations/editor/automation-editor';
import { DeleteAutomation } from '@/components/automations/editor/delete-automation';
import { getSession } from '@/lib/auth-token';
import { type Automation, getAutomation } from '@/lib/automations/api';
import { type Definition, definitionSchema } from '@/lib/automations/definition';
import { check, fromAutomation, triggerBody } from '@/lib/automations/draft';
import { getChoices, previewAutomation } from '@/lib/automations/editor-api';
import { canManage } from '@/lib/automations/run-view';
import { isUuid } from '@/lib/config/uuid';
import { isOrgAdmin } from '@/lib/org';

/** The preview of the automation as saved, which the editor shows before any edit. */
function previewSaved(a: Automation, definition: Definition) {
  const draft = fromAutomation(a, definition);
  const plan = check(draft).plan;
  return plan ? previewAutomation(plan, triggerBody(draft), a.id) : null;
}

export default async function EditAutomationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!isUuid(id)) notFound();
  const [t, te, automation, session, admin] = await Promise.all([
    getTranslations('automations'),
    getTranslations('automationEditor'),
    getAutomation(id),
    getSession(),
    isOrgAdmin(),
  ]);
  if (!automation.success && automation.error.status === 404) notFound();
  const a = automation.success ? automation.data : null;
  const definition = a ? definitionSchema.safeParse(a.definition) : null;
  const viewer = { id: session?.user.id ?? '', isOrgAdmin: admin };
  const manages = a !== null && canManage(viewer, a);
  const [choices, preview] = manages
    ? await Promise.all([
        getChoices(),
        definition?.success ? previewSaved(a, definition.data) : null,
      ])
    : [null, null];

  const crumbs = [
    { label: t('title'), href: '/automations' },
    ...(a ? [{ label: a.name, href: `/automations/${id}` }] : []),
    { label: te('edit') },
  ];
  const failed = (
    <p role="alert" className="text-negative text-ui">
      {t('loadError')}
    </p>
  );
  return (
    <AppPage
      crumbs={crumbs}
      crumbsLabel={t('breadcrumb')}
      title={a ? te('editTitle', { name: a.name }) : te('edit')}
    >
      {!a || !definition?.success ? (
        failed
      ) : !manages ? (
        <div className="flex flex-col items-start gap-2">
          <p className="text-ui">{te('readOnly')}</p>
          <Link href={`/automations/${id}`} className="text-accent-ink text-ui hover:underline">
            {te('back')}
          </Link>
        </div>
      ) : !choices?.success ? (
        <p role="alert" className="text-negative text-ui">
          {te('loadError')}
        </p>
      ) : (
        <div className="flex flex-col gap-10">
          <AutomationEditor
            choices={choices.data}
            viewerIsCreator={a.userId === viewer.id}
            saved={{ ...a, definition: definition.data }}
            initialPreview={preview?.success ? preview.data : null}
          />
          <DeleteAutomation id={a.id} name={a.name} />
        </div>
      )}
    </AppPage>
  );
}
