'use client';

import { useTranslations } from 'next-intl';
import type { ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Stepper } from '@/components/ui/stepper';
import type { ImportState, Notice } from './csv-import-state';

const STEPS = ['file', 'map', 'review'] as const;

/** The title and the three steps. While the worker imports, every step reads as done. */
export function ImportHeader({ step }: { step: ImportState['step'] }) {
  const t = useTranslations('lists');
  const at = step === 'import' ? STEPS.length : STEPS.indexOf(step);
  return (
    <DialogHeader className="border-hair flex-row flex-wrap items-center gap-3 border-b px-4 py-3.5 pr-14">
      <DialogTitle className="text-feature">{t('upload')}</DialogTitle>
      <div className="ml-auto">
        <Stepper steps={STEPS.map((s) => t(`importStep.${s}`))} current={at} />
      </div>
    </DialogHeader>
  );
}

/** The note on the left, then «Indietro», «Annulla» and the step's own button. */
export function ImportFooter({
  back,
  note,
  onCancel,
  cancelLabel,
  children,
}: {
  back?: ReactNode;
  note: string;
  onCancel: () => void;
  cancelLabel?: string;
  children?: ReactNode;
}) {
  const tc = useTranslations('common');
  return (
    <DialogFooter className="border-hair bg-raised mt-4 flex-row flex-wrap items-center justify-end gap-2 rounded-b-xl border-t px-4 py-3">
      <p className="text-muted text-small grow phone:order-first phone:basis-full">{note}</p>
      {back}
      <Button variant="outline" onClick={onCancel}>
        {cancelLabel ?? tc('cancel')}
      </Button>
      {children}
    </DialogFooter>
  );
}

/** 6e: what went wrong and what to do, inline (never a toast). */
export function ErrorNotice({ error }: { error: Notice | null }) {
  if (!error) return null;
  return (
    <div role="alert" className="bg-negative-tint text-ui mx-4 mt-4 flex gap-2.5 rounded-lg p-3.5">
      <span aria-hidden="true" className="bg-negative mt-1.75 size-2 shrink-0 rounded-full" />
      <span>
        <strong className="text-negative">{error.title}</strong> {error.body}
      </span>
    </div>
  );
}
