'use client';

import { ChevronDown } from 'lucide-react';
import { type ReactNode, useId, useState } from 'react';
import { usePhone } from '@/components/app/use-phone';
import { cn } from '@/lib/utils';

export const PLAN_EYEBROW = 'text-label text-muted uppercase';

export function PlanSection({
  title,
  summary,
  children,
}: {
  title: string;
  summary: string;
  children: ReactNode;
}) {
  const phone = usePhone();
  const [open, setOpen] = useState(false);
  const contentId = useId();
  if (!phone) {
    return (
      <div className="border-line flex flex-col gap-1 border-b px-4 pt-2.5 pb-2">
        <h4 className={PLAN_EYEBROW}>{title}</h4>
        <div>{children}</div>
      </div>
    );
  }
  return (
    <div className="border-b px-4">
      <h4 className="text-ui-strong">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={contentId}
          onClick={() => setOpen((v) => !v)}
          className="flex min-h-12 w-full items-center gap-1 text-left"
        >
          {summary}
          <ChevronDown
            aria-hidden
            className={cn('text-muted ml-auto size-4 transition-transform', open && 'rotate-180')}
          />
        </button>
      </h4>
      <div id={contentId} hidden={!open} className="pb-3">
        {children}
      </div>
    </div>
  );
}
