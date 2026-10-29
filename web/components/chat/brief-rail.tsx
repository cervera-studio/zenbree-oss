'use client';

import { useTranslations } from 'next-intl';
import { BRIEF_FIELDS, BRIEF_STAGES, type Brief, type BriefStage } from '@/lib/chat/brief';
import { cn } from '@/lib/utils';

function Undecided() {
  const t = useTranslations('chat.brief');
  return <span className="text-ui-strong text-warning">{t('undecided')}</span>;
}

export function BriefRail({
  brief,
  stage,
  onEdit,
  className,
}: {
  brief: Brief;
  stage: BriefStage;
  onEdit: () => void;
  className?: string;
}) {
  const t = useTranslations('chat.brief');
  const at = BRIEF_STAGES.indexOf(stage);
  return (
    <aside
      aria-label={t('aria')}
      className={cn(
        'border-line bg-raised flex w-chat-rail shrink-0 flex-col gap-3.5 border-l p-4',
        className,
      )}
    >
      <ol className="flex gap-1.5">
        {BRIEF_STAGES.map((s, i) => (
          <li
            key={s}
            aria-current={s === stage ? 'step' : undefined}
            className="flex flex-1 flex-col gap-1.5"
          >
            <span className={cn('h-0.75 rounded-full', i <= at ? 'bg-accent' : 'bg-line')} />
            <span className={cn('text-small', i <= at ? 'text-ink' : 'text-muted')}>
              {t(`stage.${s}`)}
            </span>
          </li>
        ))}
      </ol>

      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h2 className="text-label text-muted uppercase">{t('title')}</h2>
          <button type="button" onClick={onEdit} className="text-ui-strong text-accent-ink">
            {t('edit')}
          </button>
        </div>
        <dl className="flex flex-col gap-3">
          {BRIEF_FIELDS.filter(
            (f) => f === 'signal' || f === brief.excluded || brief[f].length > 0,
          ).map((f) => {
            const excluded = f === brief.excluded;
            return (
              <div
                key={f}
                className={cn(
                  'flex flex-col gap-1.5',
                  excluded && 'bg-warning-tint -mx-2 rounded-md p-2',
                )}
              >
                <dt className={cn('text-small', excluded ? 'text-warning' : 'text-muted')}>
                  {t(`field.${f}`)}
                </dt>
                <dd className="flex flex-wrap gap-1.5">
                  {brief[f].length === 0 && !excluded && <Undecided />}
                  {brief[f].map((chip) => (
                    <span
                      key={chip}
                      className="bg-accent-tint text-accent-ink text-ui-strong inline-flex h-6 items-center rounded-md px-2.25"
                    >
                      {chip}
                    </span>
                  ))}
                </dd>
                {excluded && <dd className="text-small text-warning">{t('excluded')}</dd>}
              </div>
            );
          })}
          <div className="flex flex-col gap-1.5">
            <dt className="text-small text-muted">{t('field.email')}</dt>
            <dd className="text-ui-strong">
              {brief.email === null ? <Undecided /> : t(`email.${brief.email}`)}
            </dd>
          </div>
        </dl>
        {brief.excluded && <p className="text-small text-muted">{t('excludedFree')}</p>}
      </div>
    </aside>
  );
}
