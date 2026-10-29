import { CircleAlert, CircleCheck, CircleDashed, Send } from 'lucide-react';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import type { ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';

type ChecklistItem = {
  key: string;
  detail: string | null;
  optional?: boolean;
  label: ReactNode;
  href: string;
  action: string;
};

/** C1b: what a first campaign needs, each with where to fix it. `null` = missing; otherwise what is there. */
export function CampaignsEmpty({
  list,
  mailbox,
  linkedin,
}: {
  list: string | null;
  mailbox: string | null;
  linkedin: string | null;
}) {
  const t = useTranslations('campaigns.emptyState');
  const checks: ChecklistItem[] = [
    { key: 'list', detail: list, label: t('list'), href: '/', action: t('findPeople') },
    {
      key: 'mailbox',
      detail: mailbox,
      label: t('mailbox'),
      href: '/settings/integrations',
      action: t('connect'),
    },
    {
      key: 'linkedin',
      detail: linkedin,
      optional: true,
      label: t.rich('linkedin', {
        m: (chunks) => <span className="text-muted">{chunks}</span>,
      }),
      href: '/settings/integrations',
      action: t('connect'),
    },
  ];
  return (
    <EmptyState
      variant="campaigns"
      icon={Send}
      title={t('title')}
      text={t('body')}
      action={
        <>
          <ul className="flex w-full max-w-140 flex-col gap-2 text-left">
            {checks.map((check) => (
              <li
                key={check.key}
                className="border-line bg-ground text-ui flex min-h-10.5 items-center gap-2.5 rounded-lg border px-3.5 py-2"
              >
                <CheckIcon check={check} />
                <span className="min-w-0 grow">{check.label}</span>
                {check.detail !== null ? (
                  <span className="text-small text-muted truncate">{check.detail}</span>
                ) : (
                  <Link
                    href={check.href}
                    className="text-accent-ink text-ui-strong shrink-0 hover:underline phone:flex phone:min-h-11 phone:items-center"
                  >
                    {check.action}
                  </Link>
                )}
              </li>
            ))}
          </ul>
          <Button asChild>
            <Link href="/lists">{t('chooseList')}</Link>
          </Button>
        </>
      }
    />
  );
}

function CheckIcon({ check }: { check: ChecklistItem }) {
  const t = useTranslations('campaigns.emptyState');
  if (check.detail !== null)
    return (
      <>
        <CircleCheck className="text-positive size-4 shrink-0" aria-hidden="true" />
        <span className="sr-only">{t('done')}</span>
      </>
    );
  if (check.optional)
    return (
      <>
        <CircleDashed className="text-muted size-4 shrink-0" aria-hidden="true" />
        <span className="sr-only">{t('optional')}</span>
      </>
    );
  return (
    <>
      <CircleAlert className="text-warning size-4 shrink-0" aria-hidden="true" />
      <span className="sr-only">{t('todo')}</span>
    </>
  );
}
