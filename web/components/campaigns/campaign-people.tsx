import Link from 'next/link';
import { useFormatter, useTranslations } from 'next-intl';
import { Badge } from '@/components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { Campaign, Target } from '@/lib/campaigns/api';
import { activity, PERSON_STATES, personState, stepsDone } from '@/lib/campaigns/detail-view';
import type { Step } from '@/lib/campaigns/steps';
import { restartDay } from '@/lib/inbox/dates';
import { cn } from '@/lib/utils';
import { HoldBadge } from './hold-badge';
import { RegenerateValues } from './regenerate-values';
import { VariantTag } from './variant-tag';

/** C3 «Persone»: step dots, state and the newest activity, a reply linking to its thread. */
export function CampaignPeople({
  campaign,
  targets,
  steps,
}: {
  campaign: Pick<Campaign, 'id' | 'status'>;
  targets: Target[] | null;
  steps: Step[];
}) {
  const stepCount = steps.length;
  const t = useTranslations('campaigns.detail');
  const format = useFormatter();
  return (
    <section aria-labelledby="campaign-people" className="flex flex-col gap-2">
      <div className="flex items-baseline gap-2.5">
        <h2 id="campaign-people" className="text-ui-strong">
          {t('peopleTitle')}
        </h2>
        {targets && targets.length > 0 && (
          <span className="text-data-sm text-muted font-mono">
            {format.number(targets.length, 'credits')}
          </span>
        )}
        {(campaign.status === 'active' || campaign.status === 'paused') && (
          <span className="ml-auto">
            <RegenerateValues campaignId={campaign.id} />
          </span>
        )}
      </div>
      {targets === null ? (
        <p className="text-negative text-sm">{t('peopleError')}</p>
      ) : targets.length === 0 ? (
        <p className="text-muted text-sm">{t('peopleEmpty')}</p>
      ) : (
        <>
          <div className="phone:hidden">
            <Table frame>
              <TableHeader>
                <TableRow>
                  <TableHead scope="col" className="w-62.5">
                    {t('person')}
                  </TableHead>
                  <TableHead scope="col" className="w-25">
                    {t('step')}
                  </TableHead>
                  <TableHead scope="col" className="w-47.5">
                    {t('state')}
                  </TableHead>
                  <TableHead scope="col">{t('activity')}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {targets.map((p) => (
                  <TableRow key={p.id}>
                    <TableCell className="whitespace-normal">
                      <Person target={p} />
                    </TableCell>
                    <TableCell>
                      <StepDots done={stepsDone(p, stepCount)} total={stepCount} />
                    </TableCell>
                    <TableCell>
                      <State target={p} stepCount={stepCount} />
                    </TableCell>
                    <TableCell className="whitespace-normal">
                      <Activity target={p} steps={steps} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <ul className="flex flex-col gap-2 desktop:hidden">
            {targets.map((p) => (
              <li
                key={p.id}
                className="border-line bg-ground text-ui flex flex-col gap-2 rounded-lg border p-3.5"
              >
                <Person target={p} />
                <div className="flex items-center justify-between gap-3">
                  <State target={p} stepCount={stepCount} />
                  <StepDots done={stepsDone(p, stepCount)} total={stepCount} />
                </div>
                <Activity target={p} steps={steps} />
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function Person({ target: p }: { target: Target }) {
  const t = useTranslations('campaigns.detail');
  const format = useFormatter();
  return (
    <span>
      <strong className="text-ui-strong text-ink">
        {[p.firstName, p.lastName].filter(Boolean).join(' ') || '—'}
      </strong>
      {p.companyName && <span className="text-muted"> {p.companyName}</span>}
      {p.signalTitle && (
        <span className="text-muted text-data-sm block">
          {t('signalReason', { title: p.signalTitle })}
        </span>
      )}
      {p.frozenAt && p.status !== 'draft' && (
        <span className="text-muted text-data-sm block">
          {t('valuesFrom', {
            at: format.dateTime(new Date(p.frozenAt), {
              day: 'numeric',
              month: 'short',
              hour: '2-digit',
              minute: '2-digit',
            }),
          })}
        </span>
      )}
    </span>
  );
}

function StepDots({ done, total }: { done: number; total: number }) {
  const t = useTranslations('campaigns.detail');
  return (
    <span className="flex items-center gap-1" role="img" aria-label={t('stepOf', { done, total })}>
      {Array.from({ length: total }, (_, i) => (
        <span key={i} className={cn('h-1 w-4 rounded-full', i < done ? 'bg-accent' : 'bg-line')} />
      ))}
    </span>
  );
}

function State({ target, stepCount }: { target: Target; stepCount: number }) {
  const t = useTranslations('campaigns.detail.states');
  const state = personState(target, stepCount);
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      <Badge tone={PERSON_STATES[state]}>{t(state)}</Badge>
      <HoldBadge target={target} />
      <VariantTag variant={target.variant} />
    </span>
  );
}

function Activity({ target, steps }: { target: Target; steps: Step[] }) {
  const t = useTranslations('campaigns.detail');
  const ti = useTranslations('inbox');
  const format = useFormatter();
  const ago = (iso: string) => format.relativeTime(new Date(iso), new Date());
  const a = activity(target);
  const thread = (id: string, text: string) => (
    <Link
      href={`/inbox?m=${encodeURIComponent(id)}`}
      className="text-accent-ink text-ui-strong hover:underline"
    >
      {text}
    </Link>
  );
  switch (a.kind) {
    case 'paused': {
      const text = t('outOfOffice', restartDay(format, a.until));
      return a.messageId ? thread(a.messageId, text) : <span className="text-muted">{text}</span>;
    }
    case 'message': {
      const what =
        a.messageKind !== 'reply'
          ? ti(`kind.${a.messageKind}`)
          : a.label
            ? t('repliedLabel', { label: ti(`label.${a.label}`) })
            : t('replied');
      return thread(a.messageId, `${what} · ${ago(a.at)}`);
    }
    case 'missing':
      return (
        <span className="text-muted">
          {t('missingValue', { tokens: a.tokens.map((token) => `{{${token}}}`).join(', ') })}
        </span>
      );
    case 'ready':
      return <span className="text-muted">{t('readyStep', { step: a.step })}</span>;
    case 'invalidEmail':
      return <span className="text-muted">{t('invalidAddress')}</span>;
    case 'sent':
      return (
        <span className="text-muted">
          {/* A task step sent nothing: say what happened instead. */}
          {t(steps[a.step - 1]?.channel === 'hubspot_task' ? 'taskStep' : 'sentStep', {
            step: a.step,
          })}{' '}
          · {ago(a.at)}
        </span>
      );
    case 'none':
      return <span className="text-muted">—</span>;
  }
}
