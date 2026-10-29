'use client';

import { Loader2 } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { useErrorText } from '@/i18n/error-text';
import type { Effect } from '@/lib/automations/draft';
import type { AppErrorJSON } from '@/lib/types';

type Props = {
  publishes: 'create' | { version: number; next: number | null };
  effects: Effect[];
  /** The campaign of the plan sends to people as soon as they reach it: a new version warns about it. */
  sends: 'campaignSends' | 'instantlySends' | null;
  disabled: boolean;
  pending: boolean;
  error: AppErrorJSON | null;
  onSave: () => Promise<void>;
};

export function SaveButton({ publishes, effects, sends, ...props }: Props) {
  const t = useTranslations('automationEditor.save');
  const errorText = useErrorText();
  const [open, setOpen] = useState(false);
  const next = publishes === 'create' ? null : publishes.next;
  const label =
    publishes === 'create' ? t('create') : next ? t('publish', { version: next }) : t('save');
  const old = publishes === 'create' ? 0 : publishes.version;
  const consequences = [
    ...effects.map((effect) => t(`effects.${effect}`, { next: next ?? old })),
    ...(next && sends ? [t(`effects.${sends}`)] : []),
  ];
  return (
    <div className="flex flex-col gap-1.5 phone:items-stretch">
      <Button
        size="lg"
        className="self-start phone:h-11 phone:self-stretch"
        disabled={props.disabled || props.pending}
        onClick={() => (consequences.length > 0 ? setOpen(true) : props.onSave())}
      >
        {props.pending && <Loader2 aria-hidden className="animate-spin" />}
        {label}
      </Button>
      {publishes === 'create' && <p className="text-small text-muted">{t('createsPaused')}</p>}
      {props.error && (
        <p role="alert" className="text-negative text-ui">
          {errorText(props.error)}
        </p>
      )}
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        destructive={false}
        title={next ? t('confirm.publishTitle', { version: next }) : t('confirm.saveTitle')}
        description={next ? t('confirm.publish', { version: old }) : t('confirm.save')}
        consequences={consequences}
        confirmLabel={label}
        onConfirm={props.onSave}
      />
    </div>
  );
}
