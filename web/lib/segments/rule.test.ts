import fc from 'fast-check';
import { describe, expect, it } from 'vite-plus/test';
import api from '@/lib/api/fixtures/segment-rule-bounds.json';
import { canonicalPicks } from './fields';
import {
  addCondition,
  addGroup,
  complete,
  emptyRule,
  nodeAt,
  nodeOf,
  type Op,
  type Pick,
  pointers,
  type RuleGroup,
  remove,
  sameRule,
  setBounds,
  setField,
  setGroupOp,
  setOp,
  setValues,
  withinBounds,
} from './rule';

const LIST = '6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b';
const CANONICAL = new Set([...api.canonical.person, ...api.canonical.company]);

function apiAccepts(root: unknown): boolean {
  let leaves = 0;
  const finite = (v: unknown) => typeof v === 'number' && Number.isFinite(v);
  const bounded = (n: Record<string, unknown>, fit: boolean) => {
    const takes: Record<string, string[]> = {
      gt: ['low'],
      gte: ['low'],
      lt: ['high'],
      lte: ['high'],
      between: ['low', 'high'],
    };
    const given = ['low', 'high'].filter((k) => n[k] !== undefined && n[k] !== null);
    if (fit && (n.op === 'is_missing' || n.op === 'is_filled')) return given.length === 0;
    if (JSON.stringify(given) !== JSON.stringify(takes[n.op as string])) return false;
    if (!given.every((k) => finite(n[k]))) return false;
    const inFit = (k: string) => (n[k] as number) >= api.fit.min && (n[k] as number) <= api.fit.max;
    if (fit && !given.every(inFit)) return false;
    return !(given.length === 2 && (n.low as number) > (n.high as number));
  };
  const field = (f: unknown) => {
    const x = f as Record<string, unknown>;
    const key = typeof x?.key === 'string' && x.key.length >= 1 && x.key.length <= 100;
    const canonical = x?.kind === 'canonical' && CANONICAL.has(x.key as string);
    return (canonical || (x?.kind === 'column' && x.list_id === LIST)) && key;
  };
  const node = (raw: unknown, depth: number): boolean => {
    const n = raw as Record<string, unknown>;
    if (n.type === 'group') {
      const kids = n.children as unknown[];
      return (
        depth <= api.max_depth &&
        (n.op === 'and' || n.op === 'or') &&
        kids.length >= 1 &&
        kids.length <= api.max_children &&
        kids.every((k) => node(k, depth + 1))
      );
    }
    leaves += 1;
    if (n.type === 'text') {
      const values = n.values as unknown[];
      const listOp = n.op === 'in' || n.op === 'not_in';
      return (
        field(n.field) &&
        ['eq', 'neq', 'contains', 'not_contains', 'in', 'not_in'].includes(n.op as string) &&
        values.length >= 1 &&
        values.length <= api.max_values &&
        (listOp || values.length === 1) &&
        values.every(
          (v) => typeof v === 'string' && v.length >= 1 && v.length <= api.max_value_length,
        )
      );
    }
    if (n.type === 'number') {
      return (
        field(n.field) &&
        ['gt', 'gte', 'lt', 'lte', 'between'].includes(n.op as string) &&
        bounded(n, false)
      );
    }
    if (n.type === 'fit') {
      return (
        ['gte', 'lte', 'between', 'is_missing', 'is_filled'].includes(n.op as string) &&
        bounded(n, true)
      );
    }
    if (n.type === 'presence') {
      return field(n.field) && (n.op === 'is_missing' || n.op === 'is_filled');
    }
    if (n.type === 'in_list')
      return n.list_id === LIST && ['in_list', 'not_in_list'].includes(n.op as string);
    return false;
  };
  return node(root, 1) && leaves <= api.max_conditions;
}

const picks: Pick[] = [
  { type: 'text', field: { kind: 'canonical', key: 'title' } },
  { type: 'number', field: { kind: 'canonical', key: 'employees' } },
  { type: 'text', field: { kind: 'column', list_id: LIST, key: 'ai_summary' } },
  { type: 'number', field: { kind: 'column', list_id: LIST, key: 'revenue' } },
  { type: 'fit' },
  { type: 'in_list', list_id: LIST },
];
const ops: Op[] = [
  'eq',
  'neq',
  'contains',
  'not_contains',
  'in',
  'not_in',
  'gt',
  'gte',
  'lt',
  'lte',
  'between',
  'is_missing',
  'is_filled',
  'in_list',
  'not_in_list',
];
const hostileString = fc.oneof(
  fc.string({ maxLength: 12 }),
  fc.constantFrom('', '   ', ' ceo ', 'CEO', 'ceo', 'x'.repeat(201), 'x'.repeat(200)),
);
const hostileNumber = fc.oneof(
  fc.double(),
  fc.constantFrom(Number.NaN, Number.POSITIVE_INFINITY, -1, 0, 50, 100, 101, 1e9),
  fc.constant(null),
);

type Edit =
  | { kind: 'addCondition' | 'addGroup' | 'setField'; at: number; pick: number }
  | { kind: 'remove'; at: number }
  | { kind: 'setGroupOp'; at: number; and: boolean }
  | { kind: 'setOp'; at: number; op: number }
  | { kind: 'setValues'; at: number; values: string[] }
  | { kind: 'setBounds'; at: number; low?: number | null; high?: number | null };

const at = fc.nat();
const pick = fc.nat();
const fair = fc.array(fc.constantFrom('ceo', 'cto', 'Milano', 'saas'), {
  minLength: 1,
  maxLength: 3,
});
const hostileValues = fc.oneof(
  fc.array(hostileString, { maxLength: 4 }),
  fc.array(fc.string({ minLength: 1, maxLength: 5 }), { minLength: 49, maxLength: 60 }),
);
const fairNumber = fc.integer({ min: 0, max: 100 });
const edit: fc.Arbitrary<Edit> = fc.oneof(
  { weight: 6, arbitrary: fc.record({ kind: fc.constant('addCondition'), at, pick }) },
  { weight: 3, arbitrary: fc.record({ kind: fc.constant('addGroup'), at, pick }) },
  { weight: 1, arbitrary: fc.record({ kind: fc.constant('setField'), at, pick }) },
  { weight: 1, arbitrary: fc.record({ kind: fc.constant('remove'), at }) },
  { weight: 1, arbitrary: fc.record({ kind: fc.constant('setGroupOp'), at, and: fc.boolean() }) },
  { weight: 2, arbitrary: fc.record({ kind: fc.constant('setOp'), at, op: fc.nat() }) },
  { weight: 4, arbitrary: fc.record({ kind: fc.constant('setValues'), at, values: fair }) },
  {
    weight: 1,
    arbitrary: fc.record({ kind: fc.constant('setValues'), at, values: hostileValues }),
  },
  {
    weight: 4,
    arbitrary: fc.record(
      { kind: fc.constant('setBounds'), at, low: fairNumber, high: fairNumber },
      { requiredKeys: ['kind', 'at'] },
    ),
  },
  {
    weight: 1,
    arbitrary: fc.record(
      { kind: fc.constant('setBounds'), at, low: hostileNumber, high: hostileNumber },
      { requiredKeys: ['kind', 'at'] },
    ),
  },
);

function apply(root: RuleGroup, e: Edit): RuleGroup {
  const all = pointers(root);
  const path = all[e.at % all.length] ?? '';
  switch (e.kind) {
    case 'addCondition':
      return addCondition(root, path, picks[e.pick % picks.length] as Pick);
    case 'addGroup':
      return addGroup(root, path, picks[e.pick % picks.length] as Pick);
    case 'setField':
      return setField(root, path, picks[e.pick % picks.length] as Pick);
    case 'remove':
      return remove(root, path);
    case 'setGroupOp':
      return setGroupOp(root, path, e.and ? 'and' : 'or');
    case 'setOp':
      return setOp(root, path, ops[e.op % ops.length] as Op);
    case 'setValues':
      return setValues(root, path, e.values);
    case 'setBounds':
      return setBounds(root, path, { low: e.low, high: e.high });
  }
}

describe('segment rule editor operations', () => {
  it('any sequence of edits, hostile inputs included, stays within the API bounds, and a complete rule is one the API accepts', () => {
    fc.assert(
      fc.property(fc.array(edit, { minLength: 20, maxLength: 150, size: 'max' }), (edits) => {
        let root = emptyRule();
        for (const e of edits) {
          root = apply(root, e);
          expect(withinBounds(root)).toBe(true);
          if (complete(root)) expect(apiAccepts(JSON.parse(JSON.stringify(root)))).toBe(true);
        }
      }),
      { numRuns: 400 },
    );
  });

  it('reaches the API depth, children and conditions, and refuses one more of each', () => {
    const title = picks[0] as Pick;
    let root = emptyRule();
    let deepest = '';
    for (let depth = 2; depth <= api.max_depth; depth++) {
      root = addGroup(root, deepest, title);
      deepest = `${deepest}/children/${(nodeAt(root, deepest) as RuleGroup).children.length - 1}`;
    }
    expect(addGroup(root, deepest, title)).toBe(root);
    let wide = emptyRule();
    for (let i = 0; i < api.max_children + 5; i++) wide = addCondition(wide, '', title);
    expect(wide.children).toHaveLength(api.max_children);
    let split = addGroup(emptyRule(), '', title);
    for (let i = 0; i < api.max_conditions; i++) {
      split = addCondition(split, '', title);
      split = addCondition(split, '/children/0', title);
    }
    expect(pointers(split).filter((p) => nodeAt(split, p)?.type === 'text')).toHaveLength(
      api.max_conditions,
    );
  });

  it('keeps as many values, characters and fit as the API, and refuses one more', () => {
    const values = Array.from({ length: api.max_values }, (_, i) =>
      `${i}`.padEnd(api.max_value_length, 'x'),
    );
    const listed = setOp(addCondition(emptyRule(), '', picks[0] as Pick), '/children/0', 'in');
    expect(nodeAt(setValues(listed, '/children/0', values), '/children/0')).toMatchObject({
      values,
    });
    expect(setValues(listed, '/children/0', [...values, 'one more'])).toBe(listed);
    expect(setValues(listed, '/children/0', [`${values[0]}x`])).toBe(listed);
    const fit = setOp(addCondition(emptyRule(), '', { type: 'fit' }), '/children/0', 'between');
    const edge = { low: api.fit.min, high: api.fit.max };
    expect(nodeAt(setBounds(fit, '/children/0', edge), '/children/0')).toMatchObject(edge);
    for (const past of [
      { ...edge, low: api.fit.min - 1 },
      { ...edge, high: api.fit.max + 1 },
    ]) {
      expect(nodeAt(setBounds(fit, '/children/0', past), '/children/0')).toEqual({
        type: 'fit',
        op: 'between',
      });
    }
  });

  it('offers exactly the canonical fields the API reads, in its order', () => {
    for (const entity of ['person', 'company'] as const) {
      const keys = canonicalPicks(entity).map((p) => ('field' in p ? p.field.key : null));
      expect(keys).toEqual(api.canonical[entity]);
    }
  });

  it('builds the Marco rule as the API stores it', () => {
    let root = emptyRule();
    root = addCondition(root, '', { type: 'text', field: { kind: 'canonical', key: 'title' } });
    root = setValues(root, '/children/0', [' ceo ']);
    root = addCondition(root, '', { type: 'text', field: { kind: 'canonical', key: 'email' } });
    root = setOp(root, '/children/1', 'is_filled');
    root = addCondition(root, '', { type: 'fit' });
    expect(complete(root)).toBe(false);
    root = setBounds(root, '/children/2', { low: 80 });
    expect(complete(root)).toBe(true);
    expect(root).toEqual({
      type: 'group',
      op: 'and',
      children: [
        {
          type: 'text',
          field: { kind: 'canonical', key: 'title' },
          op: 'contains',
          values: ['ceo'],
        },
        { type: 'presence', field: { kind: 'canonical', key: 'email' }, op: 'is_filled' },
        { type: 'fit', op: 'gte', low: 80 },
      ],
    });
  });

  it('removing the last condition of a nested group removes the group', () => {
    let root = addCondition(emptyRule(), '', picks[4] as Pick);
    root = addGroup(root, '', picks[0] as Pick);
    root = remove(root, '/children/1/children/0');
    expect(root.children).toHaveLength(1);
    expect(nodeAt(root, '/children/1')).toBeNull();
  });

  it('maps a 422 path inside a condition to the condition', () => {
    expect(nodeOf('/children/1/children/0/field/key')).toBe('/children/1/children/0');
    expect(nodeOf('/children/2/values/0')).toBe('/children/2');
    expect(nodeOf('')).toBe('');
  });
});

describe('sameRule', () => {
  it('reads a cleared and retyped bound as the stored rule', () => {
    const stored: RuleGroup = {
      type: 'group',
      op: 'and',
      children: [{ type: 'fit', op: 'gte', low: 80, high: null }],
    };
    const cleared = setBounds(stored, '/children/0', { low: null });
    const retyped = setBounds(cleared, '/children/0', { low: 80 });
    expect(sameRule(retyped, stored)).toBe(true);
    expect(sameRule(setBounds(stored, '/children/0', { low: 81 }), stored)).toBe(false);
  });
});
