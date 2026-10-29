export type Entity = 'person' | 'company';
type CanonicalField = { kind: 'canonical'; key: string };
type ColumnField = { kind: 'column'; list_id: string; key: string };
export type RuleField = CanonicalField | ColumnField;

const TEXT_OPS = ['eq', 'neq', 'contains', 'not_contains', 'in', 'not_in'] as const;
const NUMBER_OPS = ['gt', 'gte', 'lt', 'lte', 'between'] as const;
const FIT_OPS = ['gte', 'lte', 'between', 'is_missing', 'is_filled'] as const;
const PRESENCE_OPS = ['is_missing', 'is_filled'] as const;
const IN_LIST_OPS = ['in_list', 'not_in_list'] as const;
type TextOp = (typeof TEXT_OPS)[number];
type NumberOp = (typeof NUMBER_OPS)[number];
type FitOp = (typeof FIT_OPS)[number];
type PresenceOp = (typeof PRESENCE_OPS)[number];
type InListOp = (typeof IN_LIST_OPS)[number];

type TextCondition = { type: 'text'; field: RuleField; op: TextOp; values: string[] };
type NumberCondition = {
  type: 'number';
  field: RuleField;
  op: NumberOp;
  low?: number | null;
  high?: number | null;
};
type FitCondition = { type: 'fit'; op: FitOp; low?: number | null; high?: number | null };
type PresenceCondition = { type: 'presence'; field: RuleField; op: PresenceOp };
type InListCondition = { type: 'in_list'; list_id: string; op: InListOp };
export type Condition =
  | TextCondition
  | NumberCondition
  | FitCondition
  | PresenceCondition
  | InListCondition;
export type GroupOp = 'and' | 'or';
export type RuleGroup = { type: 'group'; op: GroupOp; children: RuleNode[] };
export type RuleNode = Condition | RuleGroup;
export type Op = Condition['op'];

const MAX_DEPTH = 3;
export const MAX_CONDITIONS = 20;
const MAX_CHILDREN = 20;
export const MAX_VALUES = 50;
export const MAX_VALUE_LENGTH = 200;
const FIT_MIN = 0;
const FIT_MAX = 100;

export type Bound = 'low' | 'high';
const BOUNDS: Record<NumberOp, readonly Bound[]> = {
  gt: ['low'],
  gte: ['low'],
  lt: ['high'],
  lte: ['high'],
  between: ['low', 'high'],
};
const LIST_OPS: readonly TextOp[] = ['in', 'not_in'];

export type Pick =
  | { type: 'text' | 'number'; field: RuleField }
  | { type: 'fit' }
  | { type: 'in_list'; list_id: string };

const rowKeys = new WeakMap<Condition, number>();
let lastRowKey = 0;

export function rowKey(condition: Condition): number {
  let key = rowKeys.get(condition);
  if (key === undefined) {
    key = ++lastRowKey;
    rowKeys.set(condition, key);
  }
  return key;
}

function keepRowKey<T extends Condition>(from: Condition, to: T): T {
  rowKeys.set(to, rowKey(from));
  return to;
}

export const emptyRule = (): RuleGroup => ({ type: 'group', op: 'and', children: [] });

function newCondition(pick: Pick): Condition {
  if (pick.type === 'fit') return { type: 'fit', op: 'gte' };
  if (pick.type === 'in_list') return { type: 'in_list', list_id: pick.list_id, op: 'in_list' };
  if (pick.type === 'number') return { type: 'number', field: pick.field, op: 'gte' };
  return { type: 'text', field: pick.field, op: 'contains', values: [] };
}

const readsAsNumber = (field: RuleField) => field.kind === 'column' || field.key === 'employees';

export function opsFor(condition: Condition): readonly Op[] {
  if (condition.type === 'fit') return FIT_OPS;
  if (condition.type === 'in_list') return IN_LIST_OPS;
  return readsAsNumber(condition.field)
    ? [...TEXT_OPS, ...NUMBER_OPS, ...PRESENCE_OPS]
    : [...TEXT_OPS, ...PRESENCE_OPS];
}

const isGroup = (node: RuleNode): node is RuleGroup => node.type === 'group';
const has = <T extends string>(list: readonly T[], value: string): value is T =>
  (list as readonly string[]).includes(value);

export const nodeOf = (path: string) => /^(?:\/children\/\d+)*/.exec(path)?.[0] ?? '';

function indexes(path: string): number[] | null {
  if (path === '') return [];
  if (!/^(?:\/children\/\d+)+$/.test(path)) return null;
  return path
    .split('/children/')
    .slice(1)
    .map((n) => Number(n));
}

export function nodeAt(root: RuleGroup, path: string): RuleNode | null {
  const at = indexes(path);
  if (!at) return null;
  let node: RuleNode = root;
  for (const i of at) {
    if (!isGroup(node)) return null;
    const next: RuleNode | undefined = node.children[i];
    if (!next) return null;
    node = next;
  }
  return node;
}

const depthOf = (root: RuleGroup, path: string) => {
  const node = nodeAt(root, path);
  const steps = indexes(path)?.length ?? 0;
  return node && isGroup(node) ? steps + 1 : steps;
};

function replaceAt(node: RuleGroup, at: number[], make: (old: RuleNode) => RuleNode): RuleGroup {
  const [head, ...rest] = at;
  if (head === undefined) return make(node) as RuleGroup;
  return {
    ...node,
    children: node.children.map((child, i) =>
      i !== head
        ? child
        : rest.length === 0
          ? make(child)
          : replaceAt(child as RuleGroup, rest, make),
    ),
  };
}

function update(
  root: RuleGroup,
  path: string,
  make: (old: RuleNode) => RuleNode | null,
): RuleGroup {
  const node = nodeAt(root, path);
  const at = indexes(path);
  if (!node || !at) return root;
  const next = make(node);
  if (!next) return root;
  return replaceAt(root, at, () => next);
}

export function pointers(root: RuleGroup, path = ''): string[] {
  return [
    path,
    ...root.children.flatMap((child, i) => {
      const here = `${path}/children/${i}`;
      return isGroup(child) ? pointers(child, here) : [here];
    }),
  ];
}

const conditionCount = (root: RuleGroup): number =>
  root.children.reduce((n, child) => n + (isGroup(child) ? conditionCount(child) : 1), 0);

export function canAddCondition(root: RuleGroup, groupPath: string): boolean {
  const group = nodeAt(root, groupPath);
  return (
    group !== null &&
    isGroup(group) &&
    group.children.length < MAX_CHILDREN &&
    conditionCount(root) < MAX_CONDITIONS
  );
}

export const canAddGroup = (root: RuleGroup, groupPath: string) =>
  canAddCondition(root, groupPath) && depthOf(root, groupPath) < MAX_DEPTH;

export function addCondition(root: RuleGroup, groupPath: string, pick: Pick): RuleGroup {
  if (!canAddCondition(root, groupPath)) return root;
  return update(root, groupPath, (group) =>
    isGroup(group) ? { ...group, children: [...group.children, newCondition(pick)] } : null,
  );
}

export function addGroup(root: RuleGroup, groupPath: string, pick: Pick): RuleGroup {
  if (!canAddGroup(root, groupPath)) return root;
  return update(root, groupPath, (group) => {
    if (!isGroup(group)) return null;
    const op: GroupOp = group.op === 'and' ? 'or' : 'and';
    return {
      ...group,
      children: [...group.children, { type: 'group', op, children: [newCondition(pick)] }],
    };
  });
}

export function remove(root: RuleGroup, path: string): RuleGroup {
  const at = indexes(path);
  if (!at || at.length === 0 || !nodeAt(root, path)) return root;
  const parentPath = path.slice(0, path.lastIndexOf('/children/'));
  const parent = nodeAt(root, parentPath) as RuleGroup;
  if (parent.children.length === 1 && parentPath !== '') return remove(root, parentPath);
  const index = at[at.length - 1];
  return update(root, parentPath, () => ({
    ...parent,
    children: parent.children.filter((_, i) => i !== index),
  }));
}

export function setGroupOp(root: RuleGroup, path: string, op: GroupOp): RuleGroup {
  return update(root, path, (node) => (isGroup(node) ? { ...node, op } : null));
}

export function setField(root: RuleGroup, path: string, pick: Pick): RuleGroup {
  return update(root, path, (node) =>
    isGroup(node) ? null : keepRowKey(node, newCondition(pick)),
  );
}

function keepBounds<T extends { low?: number | null; high?: number | null }>(
  condition: T,
  takes: readonly Bound[],
) {
  const out: { low?: number; high?: number } = {};
  for (const bound of takes) {
    const value = condition[bound];
    if (typeof value === 'number') out[bound] = value;
  }
  return out;
}

type FieldCondition = TextCondition | NumberCondition | PresenceCondition;

function switchFieldOp(node: FieldCondition, op: Op): Condition | null {
  const field = node.field;
  if (has(TEXT_OPS, op)) {
    const values = node.type === 'text' ? node.values : [];
    return { type: 'text', field, op, values: has(LIST_OPS, op) ? values : values.slice(0, 1) };
  }
  if (has(NUMBER_OPS, op)) {
    const bounds = node.type === 'number' ? keepBounds(node, BOUNDS[op]) : {};
    return { type: 'number', field, op, ...bounds };
  }
  return has(PRESENCE_OPS, op) ? { type: 'presence', field, op } : null;
}

function switchFitOp(node: FitCondition, op: Op): FitCondition | null {
  if (!has(FIT_OPS, op)) return null;
  if (op === 'is_missing' || op === 'is_filled') return { type: 'fit', op };
  return { type: 'fit', op, ...keepBounds(node, BOUNDS[op]) };
}

function switchOp(node: Condition, op: Op): Condition | null {
  if (node.type === 'in_list') return has(IN_LIST_OPS, op) ? { ...node, op } : null;
  if (node.type === 'fit') return switchFitOp(node, op);
  return switchFieldOp(node, op);
}

export function setOp(root: RuleGroup, path: string, op: Op): RuleGroup {
  return update(root, path, (node) => {
    if (isGroup(node) || !opsFor(node).includes(op)) return null;
    const next = switchOp(node, op);
    return next && keepRowKey(node, next);
  });
}

const goodValue = (v: string) => v.length > 0 && v.length <= MAX_VALUE_LENGTH;

export function cleanValues(values: readonly string[], most: number): string[] | null {
  const seen = new Set<string>();
  const next: string[] = [];
  for (const raw of values) {
    const value = raw.trim();
    if (!goodValue(value)) return null;
    if (seen.has(value.toLowerCase())) continue;
    seen.add(value.toLowerCase());
    next.push(value);
  }
  return next.length > most ? null : next;
}

export function setValues(root: RuleGroup, path: string, values: readonly string[]): RuleGroup {
  return update(root, path, (node) => {
    if (node.type !== 'text') return null;
    const next = cleanValues(values, has(LIST_OPS, node.op) ? MAX_VALUES : 1);
    return next && keepRowKey(node, { ...node, values: next });
  });
}

export function boundsOf(condition: Condition): readonly Bound[] {
  if (condition.type !== 'number' && condition.type !== 'fit') return [];
  return condition.op === 'is_missing' || condition.op === 'is_filled' ? [] : BOUNDS[condition.op];
}

export function setBounds(
  root: RuleGroup,
  path: string,
  bounds: { low?: number | null; high?: number | null },
): RuleGroup {
  return update(root, path, (node) => {
    if (node.type !== 'number' && node.type !== 'fit') return null;
    const cleared = { ...node };
    delete cleared.low;
    delete cleared.high;
    const next = { ...cleared };
    for (const bound of boundsOf(node)) {
      const value = bounds[bound];
      if (typeof value === 'number') next[bound] = value;
    }
    return keepRowKey(node, boundsFit(next, false) ? next : cleared);
  });
}

function boundsFit(condition: NumberCondition | FitCondition, required: boolean): boolean {
  const takes = boundsOf(condition);
  for (const bound of ['low', 'high'] as const) {
    const value = condition[bound];
    const present = value !== undefined && value !== null;
    if (present && (!takes.includes(bound) || !Number.isFinite(value))) return false;
    if (present && condition.type === 'fit' && (value < FIT_MIN || value > FIT_MAX)) return false;
    if (!present && required && takes.includes(bound)) return false;
  }
  const { low, high } = condition;
  return !(typeof low === 'number' && typeof high === 'number' && low > high);
}

function conditionFits(condition: Condition, required: boolean): boolean {
  if (condition.type === 'text') {
    const most = has(LIST_OPS, condition.op) ? MAX_VALUES : 1;
    const shaped = condition.values.every((v) => v === v.trim() && goodValue(v));
    return shaped && condition.values.length <= most && (!required || condition.values.length > 0);
  }
  if (condition.type === 'number' || condition.type === 'fit')
    return boundsFit(condition, required);
  return true;
}

function fits(root: RuleGroup, required: boolean): boolean {
  if (conditionCount(root) > MAX_CONDITIONS) return false;
  const walk = (group: RuleGroup, depth: number, isRoot: boolean): boolean =>
    depth <= MAX_DEPTH &&
    group.children.length <= MAX_CHILDREN &&
    (group.children.length > 0 || (isRoot && !required)) &&
    group.children.every((child) =>
      isGroup(child) ? walk(child, depth + 1, false) : conditionFits(child, required),
    );
  return walk(root, 1, true);
}

export const withinBounds = (root: RuleGroup) => fits(root, false);

export const complete = (root: RuleGroup) => fits(root, true);

export const sameRule = (a: RuleGroup, b: RuleGroup) => canonical(a) === canonical(b);

function canonical(rule: RuleGroup): string {
  return JSON.stringify(rule, (_, value: unknown) =>
    value && typeof value === 'object' && !Array.isArray(value)
      ? Object.fromEntries(
          Object.entries(value)
            .filter(([, v]) => v !== null && v !== undefined)
            .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)),
        )
      : value,
  );
}
