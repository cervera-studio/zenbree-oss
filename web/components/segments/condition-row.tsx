'use client';

import { Trash2, X } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { useId, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { encodePick, type FieldCatalog } from '@/lib/segments/fields';
import { formatNumber, parseNumber } from '@/lib/segments/number';
import {
  type Bound,
  boundsOf,
  type Condition,
  cleanValues,
  MAX_VALUE_LENGTH,
  MAX_VALUES,
  type Op,
  opsFor,
  type Pick,
  type RuleGroup,
  remove,
  setBounds,
  setField,
  setOp,
  setValues,
} from '@/lib/segments/rule';
import { cn } from '@/lib/utils';
import { FieldPicker } from './field-picker';

export type Edit = (change: (root: RuleGroup) => RuleGroup) => void;

const pickOf = (c: Condition): Pick =>
  c.type === 'fit'
    ? { type: 'fit' }
    : c.type === 'in_list'
      ? { type: 'in_list', list_id: c.list_id }
      : { type: c.type === 'number' ? 'number' : 'text', field: c.field };

export function ConditionRow({
  condition,
  path,
  catalog,
  marked,
  edit,
}: {
  condition: Condition;
  path: string;
  catalog: FieldCatalog;
  marked: boolean;
  edit: Edit;
}) {
  const t = useTranslations('segments.editor');
  const to = useTranslations('segments.ops');
  const id = useId();
  return (
    <li
      data-invalid={marked || undefined}
      className={cn(
        'border-line bg-ground flex flex-wrap items-start gap-2 rounded-md border p-2',
        marked && 'border-negative',
      )}
    >
      <FieldPicker
        aria-label={t('field')}
        className="min-w-40 flex-1 phone:basis-full"
        catalog={catalog}
        value={encodePick(pickOf(condition))}
        placeholder={t('pickField')}
        onPick={(pick) => edit((r) => setField(r, path, pick))}
      />
      <Select
        aria-label={t('op')}
        touch
        className="w-44 phone:w-auto phone:flex-1"
        value={condition.op}
        onChange={(e) => edit((r) => setOp(r, path, e.target.value as Op))}
      >
        {!opsFor(condition).includes(condition.op) && (
          <option value={condition.op}>{to(condition.op)}</option>
        )}
        {opsFor(condition).map((op) => (
          <option key={op} value={op}>
            {to(op)}
          </option>
        ))}
      </Select>
      <Value
        key={`${encodePick(pickOf(condition))} ${condition.op}`}
        condition={condition}
        path={path}
        edit={edit}
        id={id}
      />
      <Button
        variant="ghost"
        size="icon-md"
        className="phone:size-11"
        aria-label={t('remove')}
        onClick={() => edit((r) => remove(r, path))}
      >
        <Trash2 aria-hidden />
      </Button>
      {marked && (
        <p role="alert" className="text-negative text-small basis-full">
          {t('invalid')}
        </p>
      )}
    </li>
  );
}

function Value({
  condition,
  path,
  edit,
  id,
}: {
  condition: Condition;
  path: string;
  edit: Edit;
  id: string;
}) {
  if (condition.type === 'text') {
    return condition.op === 'in' || condition.op === 'not_in' ? (
      <ManyValues values={condition.values} path={path} edit={edit} />
    ) : (
      <OneValue value={condition.values[0] ?? ''} path={path} edit={edit} />
    );
  }
  if (condition.type !== 'number' && condition.type !== 'fit') return null;
  return boundsOf(condition).length > 0 ? (
    <Bounds condition={condition} path={path} edit={edit} id={id} />
  ) : null;
}

function OneValue({ value, path, edit }: { value: string; path: string; edit: Edit }) {
  const t = useTranslations('segments.editor');
  const [raw, setRaw] = useState(value);
  const shown = raw.trim() === value ? raw : value;
  return (
    <Input
      aria-label={t('value')}
      className="min-w-32 flex-1 phone:h-11 phone:basis-full"
      maxLength={MAX_VALUE_LENGTH}
      value={shown}
      onChange={(e) => {
        setRaw(e.target.value);
        const next = e.target.value.trim();
        edit((r) => setValues(r, path, next ? [next] : []));
      }}
    />
  );
}

function ManyValues({ values, path, edit }: { values: string[]; path: string; edit: Edit }) {
  const t = useTranslations('segments.editor');
  const limitId = useId();
  const [draft, setDraft] = useState('');
  const [refused, setRefused] = useState(false);
  const full = values.length >= MAX_VALUES;
  const add = () => {
    const parts = draft
      .split(',')
      .map((v) => v.trim())
      .filter(Boolean);
    if (parts.length === 0) return;
    const next = cleanValues([...values, ...parts], MAX_VALUES);
    if (!next) {
      setRefused(true);
      return;
    }
    edit((r) => setValues(r, path, next));
    setDraft('');
  };
  return (
    <span className="flex min-w-48 flex-1 flex-col gap-1.5 phone:basis-full">
      {values.length > 0 && (
        <ul className="flex flex-wrap gap-1">
          {values.map((v) => (
            <li
              key={v}
              className="bg-hair text-ink text-small flex items-center gap-1 rounded-sm pl-2"
            >
              <span className="break-all">{v}</span>
              <Button
                variant="ghost"
                size="icon-xs"
                className="phone:size-11"
                aria-label={t('removeValue', { value: v })}
                onClick={() =>
                  edit((r) =>
                    setValues(
                      r,
                      path,
                      values.filter((x) => x !== v),
                    ),
                  )
                }
              >
                <X aria-hidden />
              </Button>
            </li>
          ))}
        </ul>
      )}
      <Input
        aria-label={t('addValue')}
        aria-invalid={refused || undefined}
        aria-describedby={refused ? limitId : undefined}
        placeholder={full ? t('valuesFull', { n: MAX_VALUES }) : t('addValueHint')}
        disabled={full}
        className="phone:h-11"
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          setRefused(false);
        }}
        onBlur={add}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault();
            add();
          }
        }}
      />
      {refused && (
        <p id={limitId} role="alert" className="text-negative text-small">
          {t('valuesLimit', { n: MAX_VALUES, max: MAX_VALUE_LENGTH })}
        </p>
      )}
    </span>
  );
}

function Bounds({
  condition,
  path,
  edit,
  id,
}: {
  condition: Extract<Condition, { type: 'number' | 'fit' }>;
  path: string;
  edit: Edit;
  id: string;
}) {
  const t = useTranslations('segments.editor');
  const locale = useLocale();
  const written = (n: number | null | undefined) =>
    typeof n === 'number' ? formatNumber(n, locale) : '';
  const [text, setText] = useState<Record<Bound, string>>(() => ({
    low: written(condition.low),
    high: written(condition.high),
  }));
  const sendBothBounds = (bound: Bound, raw: string) => {
    const next = { ...text, [bound]: raw };
    setText(next);
    edit((r) =>
      setBounds(r, path, {
        low: parseNumber(next.low, locale),
        high: parseNumber(next.high, locale),
      }),
    );
  };
  return (
    <span className="flex flex-1 items-center gap-2 phone:basis-full">
      {boundsOf(condition).map((bound) => {
        const typed = parseNumber(text[bound], locale);
        const invalid = text[bound].trim() !== '' && (typed === null || typed !== condition[bound]);
        return (
          <Input
            key={bound}
            id={`${id}-${bound}`}
            aria-label={t(bound)}
            type="text"
            inputMode="decimal"
            placeholder={t(bound)}
            className="w-28 phone:h-11 phone:flex-1"
            value={text[bound]}
            aria-invalid={invalid || undefined}
            onChange={(e) => sendBothBounds(bound, e.target.value)}
          />
        );
      })}
    </span>
  );
}
