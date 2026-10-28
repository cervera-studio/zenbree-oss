'use client';

import { Loader2, Upload, X } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { type ChangeEvent, type FormEvent, useState, useTransition } from 'react';
import {
  addExclusionsAction,
  deleteExclusionAction,
} from '@/app/(app)/settings/exclusions/actions';
import { Tag } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { toast } from '@/components/ui/sonner';
import { CellLabel, StackedTable } from '@/components/ui/stacked-table';
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { useErrorText } from '@/i18n/error-text';
import type { Exclusion } from '@/lib/exclusions/api';
import {
  EXCLUSION_KINDS,
  type ExclusionInput,
  type ExclusionKind,
  MAX_EXCLUSIONS_PER_IMPORT,
  parseExclusionsCsv,
} from '@/lib/exclusions/csv';

export function ExclusionsManager({
  items,
  loadFailed,
}: {
  items: Exclusion[];
  loadFailed: boolean;
}) {
  const t = useTranslations('exclusions');
  const errorText = useErrorText();
  const tc = useTranslations('common');
  const [kind, setKind] = useState<ExclusionKind>('domain');
  const [value, setValue] = useState('');
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [pending, startTransition] = useTransition();

  function submit(inputs: ExclusionInput[], onDone?: () => void) {
    setError('');
    setNotice('');
    startTransition(async () => {
      const result = await addExclusionsAction(inputs);
      if (!result.success) {
        setError(
          result.error.code === 'EXCLUSIONS.INVALID_VALUE'
            ? t('invalidValue')
            : errorText(result.error, t('actionError')),
        );
        return;
      }
      const { added, invalid, invalidCount } = result.data;
      setNotice(
        invalidCount
          ? t('addedWithInvalid', { added, invalidCount, invalid: invalid.join(', ') })
          : t('added', { added }),
      );
      onDone?.();
    });
  }

  function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    submit([{ kind, value }], () => setValue(''));
  }

  async function importFile(event: ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget;
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    const parsed = parseExclusionsCsv(await file.text());
    if (parsed.length === 0) {
      setNotice('');
      setError(t('importEmpty'));
      return;
    }
    if (parsed.length > MAX_EXCLUSIONS_PER_IMPORT) {
      setNotice('');
      setError(t('importTooLarge', { max: MAX_EXCLUSIONS_PER_IMPORT }));
      return;
    }
    submit(parsed);
  }

  // Reversible (P1): remove at once, «Annulla» adds the same value back.
  function remove(item: Exclusion) {
    setError('');
    setNotice('');
    startTransition(async () => {
      const result = await deleteExclusionAction(item.id);
      if (!result.success) return setError(errorText(result.error, t('actionError')));
      toast.success(t('removed', { value: item.value }), {
        action: {
          label: tc('undo'),
          onClick: () => submit([{ kind: item.kind, value: item.value }]),
        },
      });
    });
  }

  return (
    <div className="flex flex-col gap-5">
      <form
        className="border-line bg-raised flex flex-col gap-3 rounded-lg border p-4 desktop:flex-row desktop:items-end"
        onSubmit={add}
      >
        <div className="flex flex-col gap-1.5 desktop:w-50">
          <label className="text-small" htmlFor="exclusion-kind">
            {t('kindLabel')}
          </label>
          <Select
            touch
            id="exclusion-kind"
            value={kind}
            onChange={(event) => setKind(event.target.value as ExclusionKind)}
          >
            {EXCLUSION_KINDS.map((k) => (
              <option key={k} value={k}>
                {t(`kind.${k}`)}
              </option>
            ))}
          </Select>
        </div>
        <div className="flex min-w-0 grow flex-col gap-1.5">
          <label className="text-small" htmlFor="exclusion-value">
            {t('valueLabel')}
          </label>
          <Input
            id="exclusion-value"
            className="phone:h-11"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder={t(`placeholder.${kind}`)}
            maxLength={500}
            required
          />
        </div>
        <Button type="submit" className="phone:h-11" disabled={pending || !value.trim()}>
          {pending && <Loader2 className="animate-spin" />}
          {t('add')}
        </Button>
        <Button asChild variant="outline" className="phone:h-11">
          <label>
            <Upload aria-hidden="true" />
            {t('import')}
            <input
              type="file"
              accept=".csv,.txt,text/csv,text/plain"
              className="sr-only"
              disabled={pending}
              onChange={(event) => void importFile(event)}
            />
          </label>
        </Button>
      </form>

      {error && (
        <p role="alert" className="text-negative text-ui-strong">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="text-positive text-ui-strong">
          {notice}
        </p>
      )}
      {loadFailed ? (
        <p role="alert" className="text-muted text-ui">
          {t('loadError')}
        </p>
      ) : items.length === 0 ? (
        <p className="text-muted text-ui">{t('empty')}</p>
      ) : (
        <StackedTable frame>
          <TableHeader>
            <TableRow>
              <TableHead scope="col" className="w-50">
                {t('kindLabel')}
              </TableHead>
              <TableHead scope="col">{t('valueLabel')}</TableHead>
              <TableHead scope="col">{t('originLabel')}</TableHead>
              <TableHead scope="col" className="w-12">
                <span className="sr-only">{t('actions')}</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <TableRow key={item.id}>
                <TableCell>
                  <CellLabel>{t('kindLabel')}</CellLabel>
                  <Tag tone="neutral">{t(`kind.${item.kind}`)}</Tag>
                </TableCell>
                <TableCell className="text-ui-strong text-ink phone:order-last phone:col-span-2">
                  <CellLabel>{t('valueLabel')}</CellLabel>
                  {item.value}
                </TableCell>
                <TableCell className="text-muted phone:order-last phone:col-span-2">
                  <CellLabel>{t('originLabel')}</CellLabel>
                  {t(`origin.${item.origin}`)}
                </TableCell>
                <TableCell>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    className="text-muted"
                    disabled={pending}
                    aria-label={t('removeLabel', { value: item.value })}
                    onClick={() => remove(item)}
                  >
                    <X aria-hidden="true" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </StackedTable>
      )}
      <p className="text-muted text-small">{t('importHelp')}</p>
    </div>
  );
}
