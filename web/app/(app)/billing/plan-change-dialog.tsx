'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { changePlanAction } from './actions';

// An upgrade charges the card at once and a downgrade changes the next renewal: both confirm first.
export function PlanChangeDialog({
  planId,
  label,
  title,
  description,
  confirmLabel,
}: {
  planId: string;
  label: string;
  title: string;
  description: string;
  confirmLabel: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button className="self-start" onClick={() => setOpen(true)}>
        {label}
      </Button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        destructive={false}
        title={title}
        description={description}
        confirmLabel={confirmLabel}
        onConfirm={() => changePlanAction(planId)}
      />
    </>
  );
}
