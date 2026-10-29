'use client';

import { Check, HelpCircle, Loader2, X } from 'lucide-react';
import { useFormatter, useNow, useTranslations } from 'next-intl';
import { useState } from 'react';
import { whyAction } from '@/app/(app)/segments/actions';
import { usePhone } from '@/components/app/use-phone';
import { Badge } from '@/components/ui/badge';
import { BottomSheetContent } from '@/components/ui/bottom-sheet';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useErrorText } from '@/i18n/error-text';
import { unreachable } from '@/lib/api/error';
import type { Evidence, Why, WhyNode } from '@/lib/segments/api';
import type { FieldCatalog } from '@/lib/segments/fields';
import { type AppErrorJSON, fail } from '@/lib/types';
import { useConditionText } from './labels';

type State = { why: Why } | { error: AppErrorJSON } | null;

export function WhyButton({
  segmentId,
  identityId,
  catalog,
}: {
  segmentId: string;
  identityId: string;
  catalog: FieldCatalog;
}) {
  const t = useTranslations('segments.why');
  const phone = usePhone();
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<State>(null);

  async function show() {
    setOpen(true);
    setState(null);
    const result = await whyAction(segmentId, identityId).catch(() => fail(unreachable()));
    setState(result.success ? { why: result.data } : { error: result.error });
  }

  const body = (
    <>
      <DialogHeader>
        <DialogTitle>{t('title')}</DialogTitle>
        <DialogDescription>{t('description')}</DialogDescription>
      </DialogHeader>
      <WhyBody state={state} catalog={catalog} />
    </>
  );
  return (
    <>
      <Button variant="ghost" size="sm" className="shrink-0 phone:h-11" onClick={show}>
        <HelpCircle aria-hidden />
        {t('open')}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        {phone ? (
          <BottomSheetContent onDismiss={() => setOpen(false)}>{body}</BottomSheetContent>
        ) : (
          <DialogContent className="max-h-4/5 overflow-y-auto sm:max-w-2xl">{body}</DialogContent>
        )}
      </Dialog>
    </>
  );
}

function WhyBody({ state, catalog }: { state: State; catalog: FieldCatalog }) {
  const t = useTranslations('segments.why');
  const errorText = useErrorText();
  if (!state) {
    return (
      <p role="status" className="text-ink text-ui flex items-center gap-2">
        <Loader2 aria-hidden className="size-4 animate-spin" />
        {t('loading')}
      </p>
    );
  }
  if ('error' in state) {
    return (
      <p role="alert" className="text-negative text-ui">
        {errorText(state.error)}
      </p>
    );
  }
  const { why } = state;
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <p className="text-ink text-ui">
        {why.matches ? t('matches') : t('noMatch')} {why.member ? t('member') : t('notMember')}
      </p>
      {why.pending && <p className="text-ink text-small">{t('pending')}</p>}
      {why.problems.length > 0 && (
        <p className="bg-warning-tint text-ink text-ui rounded-md p-3">{t('frozen')}</p>
      )}
      <Node node={why.rule} catalog={catalog} />
    </div>
  );
}

function Verdict({ value }: { value: boolean }) {
  const t = useTranslations('segments.why');
  return (
    <Badge tone={value ? 'positive' : 'neutral'}>
      {value ? <Check aria-hidden className="size-3" /> : <X aria-hidden className="size-3" />}
      {value ? t('true') : t('false')}
    </Badge>
  );
}

function Node({ node, catalog }: { node: WhyNode; catalog: FieldCatalog }) {
  const t = useTranslations('segments.why');
  const conditionText = useConditionText(catalog);
  if (node.type === 'group') {
    return (
      <section className="border-line flex flex-col gap-2 rounded-md border p-3">
        <h3 className="text-ui-strong flex flex-wrap items-center gap-2">
          {node.op === 'or' ? t('any') : t('all')}
          <Verdict value={node.value} />
        </h3>
        <ul className="flex flex-col gap-2">
          {node.children.map((child) => (
            <li key={child.path}>
              <Node node={child} catalog={catalog} />
            </li>
          ))}
        </ul>
      </section>
    );
  }
  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-ink text-ui flex flex-wrap items-center gap-2">
        <span className="min-w-0 break-words">
          {node.condition ? conditionText(node.condition) : node.path}
        </span>
        <Verdict value={node.value} />
      </p>
      {node.rows.length === 0 ? (
        <p className="text-ink text-small">{t('noRows')}</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {node.rows.map((row) => (
            <EvidenceRow key={row.row_id} row={row} />
          ))}
        </ul>
      )}
    </div>
  );
}

const shown = (row: Evidence) => {
  const v = row.value ?? row.relevance_score;
  if (v === null || v === undefined || v === '') return null;
  return typeof v === 'object' ? JSON.stringify(v) : String(v);
};

function EvidenceRow({ row }: { row: Evidence }) {
  const t = useTranslations('segments.why');
  const format = useFormatter();
  const now = useNow();
  const at = row.at ? new Date(row.at) : null;
  const known = at && !Number.isNaN(at.getTime()) ? at : null;
  return (
    <li className="bg-side text-small flex flex-wrap items-baseline gap-x-3 gap-y-0.5 rounded-sm px-2 py-1.5">
      <span className={row.hit ? 'text-positive font-semibold' : 'text-soft'}>
        {row.hit ? t('hit') : t('miss')}
      </span>
      <span className="text-ink font-semibold">{row.list_name}</span>
      <span className="text-ink min-w-0 break-words">{shown(row) ?? t('empty')}</span>
      {known && (
        <time
          dateTime={known.toISOString()}
          title={format.dateTime(known, { dateStyle: 'medium', timeStyle: 'short' })}
          className="text-soft"
        >
          {t('age', { when: format.relativeTime(known, now) })}
        </time>
      )}
    </li>
  );
}
