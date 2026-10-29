'use client';

import { Trash2 } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { SegmentedControl } from '@/components/ui/segmented-control';
import type { FieldCatalog } from '@/lib/segments/fields';
import {
  addCondition,
  addGroup,
  canAddCondition,
  canAddGroup,
  type GroupOp,
  MAX_CONDITIONS,
  type RuleGroup,
  remove,
  rowKey,
  setGroupOp,
} from '@/lib/segments/rule';
import { cn } from '@/lib/utils';
import { ConditionRow, type Edit } from './condition-row';
import { FieldPicker } from './field-picker';

export function RuleEditor({
  rule,
  catalog,
  marked,
  edit,
}: {
  rule: RuleGroup;
  catalog: FieldCatalog;
  marked: string | null;
  edit: Edit;
}) {
  return <Group group={rule} path="" root={rule} catalog={catalog} marked={marked} edit={edit} />;
}

function Group({
  group,
  path,
  root,
  catalog,
  marked,
  edit,
}: {
  group: RuleGroup;
  path: string;
  root: RuleGroup;
  catalog: FieldCatalog;
  marked: string | null;
  edit: Edit;
}) {
  const t = useTranslations('segments.editor');
  const nested = path !== '';
  const full = !canAddCondition(root, path);
  return (
    <fieldset
      className={cn(
        'flex min-w-0 flex-col gap-2',
        nested && 'border-line bg-side rounded-md border p-2',
        marked === path && nested && 'border-negative',
      )}
    >
      <legend className="sr-only">{nested ? t('groupLegend') : t('ruleLegend')}</legend>
      <div className="flex flex-wrap items-center gap-2">
        <SegmentedControl<GroupOp>
          label={t('join')}
          options={[
            { value: 'and', label: t('all') },
            { value: 'or', label: t('any') },
          ]}
          value={group.op}
          onChange={(op) => edit((r) => setGroupOp(r, path, op))}
        />
        {nested && (
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto phone:h-11"
            onClick={() => edit((r) => remove(r, path))}
          >
            <Trash2 aria-hidden />
            {t('removeGroup')}
          </Button>
        )}
      </div>
      {group.children.length === 0 ? (
        <p className="text-ink text-ui">{t('emptyRule')}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {group.children.map((child, i) => {
            const here = `${path}/children/${i}`;
            return child.type === 'group' ? (
              <li key={here}>
                <Group
                  group={child}
                  path={here}
                  root={root}
                  catalog={catalog}
                  marked={marked}
                  edit={edit}
                />
              </li>
            ) : (
              <ConditionRow
                key={rowKey(child)}
                condition={child}
                path={here}
                catalog={catalog}
                marked={marked === here}
                edit={edit}
              />
            );
          })}
        </ul>
      )}
      <div className="flex flex-wrap gap-2">
        <FieldPicker
          aria-label={t('addCondition')}
          className="w-56 phone:w-full"
          catalog={catalog}
          value=""
          placeholder={t('addCondition')}
          disabled={full}
          onPick={(pick) => edit((r) => addCondition(r, path, pick))}
        />
        {canAddGroup(root, path) && (
          <FieldPicker
            aria-label={t('addGroup')}
            className="w-56 phone:w-full"
            catalog={catalog}
            value=""
            placeholder={t('addGroup')}
            onPick={(pick) => edit((r) => addGroup(r, path, pick))}
          />
        )}
      </div>
      {full && !nested && <p className="text-ink text-small">{t('full', { n: MAX_CONDITIONS })}</p>}
    </fieldset>
  );
}
