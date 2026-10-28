'use client';

import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { Input } from '@/components/ui/input';
import { SegmentedControl } from '@/components/ui/segmented-control';
import { MAX_CREDIT_CAP } from '@/lib/automations/definition';
import type { Draft, Trigger } from '@/lib/automations/draft';
import type { Choices } from '@/lib/automations/editor-api';
import { ChoiceField, Labelled, NumberField } from './fields';
import { SignalFields } from './signal-fields';

type When = 'manual' | 'daily' | 'weekly' | 'webhook' | 'signal';
const WHEN: readonly When[] = ['manual', 'daily', 'weekly', 'webhook', 'signal'];

const toWhen = (trigger: Trigger): When =>
  trigger.kind === 'schedule' ? trigger.cadence : trigger.kind;
const toTrigger = (when: When): Trigger =>
  when === 'daily' || when === 'weekly' ? { kind: 'schedule', cadence: when } : { kind: when };

export function BasicsFields({
  draft,
  choices,
  set,
  watchlistDeleted,
}: {
  draft: Draft;
  choices: Choices;
  set: (patch: Partial<Draft>) => void;
  watchlistDeleted: boolean;
}) {
  const t = useTranslations('automationEditor.fields');
  const tt = useTranslations('automations.trigger');
  const when = toWhen(draft.trigger);
  return (
    <div className="flex flex-col gap-5">
      <Labelled label={t('name')}>
        {({ id }) => (
          <Input
            id={id}
            maxLength={120}
            className="phone:h-11"
            value={draft.name}
            onChange={(e) => set({ name: e.target.value })}
          />
        )}
      </Labelled>
      <div className="flex flex-col gap-1.5">
        <span className="text-sm font-medium">{t('trigger')}</span>
        <SegmentedControl
          label={t('trigger')}
          options={WHEN.map((value) => ({
            value,
            label: tt(value),
          }))}
          value={when}
          onChange={(value) => set({ trigger: toTrigger(value) })}
        />
        <p className="text-small text-muted">{t(`when.${when}`)}</p>
      </div>
      {when === 'signal' && (
        <SignalFields
          fields={draft.signal}
          watchlists={choices.watchlists}
          watchlistDeleted={watchlistDeleted}
          set={(patch) => set({ signal: { ...draft.signal, ...patch } })}
        />
      )}
      <ChoiceField
        label={t('list')}
        hint={
          choices.people.length > 0
            ? t('listHint')
            : t.rich('listEmpty', {
                link: (chunks) => (
                  <Link href="/" className="text-accent-ink underline">
                    {chunks}
                  </Link>
                ),
              })
        }
        value={draft.listId}
        choices={choices.people}
        onChange={(listId) => set({ listId })}
      />
      <NumberField
        label={t('cap')}
        hint={t('capHint', { max: MAX_CREDIT_CAP })}
        value={draft.creditCap}
        min={1}
        max={MAX_CREDIT_CAP}
        onChange={(creditCap) => set({ creditCap })}
      />
    </div>
  );
}
