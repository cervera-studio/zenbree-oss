'use client';

import { Loader2, Trash2 } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { Children, type ReactNode, useRef, useTransition } from 'react';
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogTitle,
} from './alert-dialog';
import { Button } from './button';

/**
 * The app's one confirm step (P1). Use it only for irreversible or shared-impact
 * actions; reversible ones run at once and offer «Annulla» in a toast instead.
 * The dialog stays open, with a spinner, until `onConfirm` settles. `consequences` is the
 * short list of what the action also does, e.g. what stops working. `confirmDisabled` holds the
 * button until a typed confirmation in `children` matches.
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  onConfirm,
  destructive = true,
  confirmDisabled = false,
  consequences,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description: ReactNode;
  confirmLabel: ReactNode;
  onConfirm: () => unknown;
  destructive?: boolean;
  confirmDisabled?: boolean;
  consequences?: ReactNode[];
  children?: ReactNode;
}) {
  const t = useTranslations('common');
  const [pending, start] = useTransition();
  // Callers open it from state, with no Radix trigger, so Radix would give focus back to nothing.
  const opener = useRef<Element | null>(null);

  return (
    <AlertDialog open={open} onOpenChange={(next) => !pending && onOpenChange(next)}>
      <AlertDialogContent
        onOpenAutoFocus={() => {
          opener.current = document.activeElement;
        }}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          if (opener.current instanceof HTMLElement) opener.current.focus();
        }}
      >
        {destructive && (
          <span
            aria-hidden
            className="bg-negative/12 text-negative flex size-10 items-center justify-center rounded-full"
          >
            <Trash2 className="size-5" />
          </span>
        )}
        <AlertDialogTitle>{title}</AlertDialogTitle>
        <AlertDialogDescription>{description}</AlertDialogDescription>
        {consequences && consequences.length > 0 && (
          <ul className="text-muted flex list-disc flex-col gap-1 pl-4.5 text-xs-plus leading-normal">
            {Children.map(consequences, (item) => (
              <li>{item}</li>
            ))}
          </ul>
        )}
        {children}
        <AlertDialogFooter>
          <AlertDialogCancel className="text-xs-plus font-semibold" disabled={pending}>
            {t('cancel')}
          </AlertDialogCancel>
          <Button
            variant={destructive ? 'negative' : 'primary'}
            className="text-xs-plus font-semibold"
            disabled={pending || confirmDisabled}
            onClick={() =>
              start(async () => {
                await onConfirm();
                onOpenChange(false);
              })
            }
          >
            {pending && <Loader2 className="size-3.5 animate-spin" />}
            {confirmLabel}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
