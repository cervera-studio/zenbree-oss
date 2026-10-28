import { IMPORT_TARGETS } from '@/lib/lists/columns';
import type { Entity, Pick, RuleField } from './rule';

export type FieldList = { id: string; name: string; entity: Entity };
export type ListColumn = { key: string; label: string };

export type FieldCatalog = {
  entity: Entity;
  lists: FieldList[];
  columns: Record<string, ListColumn[]>;
};

const pickOfField = (field: RuleField): Pick => ({
  type: field.kind === 'canonical' && field.key === 'employees' ? 'number' : 'text',
  field,
});

export function encodePick(pick: Pick): string {
  if (pick.type === 'fit') return 'fit';
  if (pick.type === 'in_list') return `in:${pick.list_id}`;
  return pick.field.kind === 'canonical'
    ? `canonical:${pick.field.key}`
    : `column:${pick.field.list_id}:${pick.field.key}`;
}

export const canonicalPicks = (entity: Entity): Pick[] =>
  IMPORT_TARGETS[entity].map((key) => pickOfField({ kind: 'canonical', key }));

export const columnPicks = (listId: string, columns: readonly ListColumn[]): Pick[] =>
  columns.map((c) => pickOfField({ kind: 'column', list_id: listId, key: c.key }));

export function listsNamed(node: unknown): string[] {
  if (!node || typeof node !== 'object') return [];
  const n = node as {
    list_id?: unknown;
    field?: { list_id?: unknown };
    children?: unknown[];
  };
  const here = [n.list_id, n.field?.list_id].filter((v): v is string => typeof v === 'string');
  return [...new Set([...here, ...(n.children ?? []).flatMap(listsNamed)])];
}
