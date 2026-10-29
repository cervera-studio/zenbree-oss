import { afterEach, describe, expect, it, vi } from 'vite-plus/test';
import { succeed } from '@/lib/types';

vi.mock('server-only', () => ({}));
vi.mock('@/lib/auth-token', () => ({ getToken: vi.fn(async () => 'token') }));
vi.mock('@/lib/projects/server', () => ({
  getProjects: vi.fn(async () => succeed({ projects: [], active: { id: 'p1' } })),
}));
process.env.API_BASE_URL = 'http://api.test';
const api = await import('./api');

const LIST = '6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b';
const RULE = {
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
    {
      type: 'group',
      op: 'or',
      children: [
        { type: 'fit', op: 'gte', low: 80, high: null },
        { type: 'in_list', list_id: LIST, op: 'in_list' },
      ],
    },
  ],
};
const SEGMENT = {
  id: 's1',
  name: 'CEO con email',
  entity: 'person',
  rule: RULE,
  version: 1,
  published_at: null,
  created_at: '2026-10-20T10:00:00Z',
  created_by: 'u1',
  members: 0,
  problems: [
    { path: '/children/2/children/1', reason: 'list_not_found', list_id: LIST, key: null },
  ],
};
const ROW = {
  id: 'r1',
  list_id: LIST,
  cells: { full_name: 'Marco Bianchi', title: 'CEO' },
  relevance_score: 85,
};
const PREVIEW = {
  count: 2,
  pending: 0,
  unidentified_rows: 1,
  linking_rows: 0,
  sample: [{ identity_id: 'i1', row: ROW }],
  joining: null,
  leaving: null,
  max_credits: null,
};
const APPLIED = { joined: 2, entered: 0, exited: 0, merged: 0, members: 2 };
const EVIDENCE = {
  list_id: LIST,
  list_name: 'Segmenti A',
  row_id: 'r1',
  hit: true,
  value: 'CEO',
  at: '2026-10-19T10:00:00Z',
  source: null,
  status: null,
  relevance_score: 85,
};
const CONDITION_NODE = {
  path: '/children/0',
  type: 'condition',
  value: true,
  op: null,
  children: [],
  condition: RULE.children[0],
  rows: [EVIDENCE],
};
const WHY = {
  identity_id: 'i1',
  matches: true,
  pending: false,
  member: true,
  rule: {
    path: '',
    type: 'group',
    value: true,
    op: 'and',
    children: [CONDITION_NODE],
    condition: null,
    rows: [],
  },
  problems: [],
};

function answer(body: unknown) {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }),
  );
}

type Read = () => Promise<{ success: boolean; error?: { code: string } }>;

const cases: [string, Read, unknown, (body: never) => unknown][] = [
  [
    'list',
    () => api.listSegments(),
    { segments: [SEGMENT] },
    () => ({ segments: [{ ...SEGMENT, version: '1' }] }),
  ],
  ['get', () => api.getSegment('s1'), SEGMENT, () => ({ ...SEGMENT, problems: undefined })],
  [
    'create',
    () => api.createSegment({ name: 'x', entity: 'person', rule: RULE as never }),
    SEGMENT,
    () => ({ ...SEGMENT, rule: { ...RULE, children: [{ type: 'text', op: 'eq' }] } }),
  ],
  [
    'edit',
    () => api.editSegment('s1', { version: 1, name: 'y' }),
    { segment: SEGMENT, joining: 3, leaving: 1, max_credits: 0 },
    () => ({ segment: SEGMENT, joining: 3, leaving: 1 }),
  ],
  [
    'publish',
    () => api.publishSegment('s1', false),
    { segment: SEGMENT, applied: APPLIED, max_credits: 0 },
    () => ({ segment: SEGMENT, applied: { ...APPLIED, entered: null }, max_credits: 0 }),
  ],
  [
    'refresh',
    () => api.refreshSegment('s1'),
    { segment: SEGMENT, applied: APPLIED, max_credits: 0 },
    () => ({ segment: { ...SEGMENT, entity: 'account' }, applied: APPLIED, max_credits: 0 }),
  ],
  [
    'preview',
    () => api.previewSegment({ entity: 'person', rule: RULE as never }),
    PREVIEW,
    () => ({ ...PREVIEW, unidentified_rows: undefined }),
  ],
  [
    'members',
    () => api.getMembers('s1', null),
    {
      members: [{ identity_id: 'i1', episode: 1, entered_at: '2026-10-20T10:00:00Z', row: ROW }],
      next_cursor: 'i1',
    },
    () => ({ members: [{ identity_id: 'i1', episode: 1, row: ROW }], next_cursor: null }),
  ],
  [
    'why',
    () => api.getWhy('s1', 'i1'),
    WHY,
    () => ({
      ...WHY,
      rule: { ...WHY.rule, children: [{ path: '/children/0', type: 'condition' }] },
    }),
  ],
];

describe('segments API reads', () => {
  afterEach(() => vi.restoreAllMocks());

  it.each(cases)('%s parses a realistic answer', async (_, read, good) => {
    answer(good);
    expect((await read()).success).toBe(true);
  });

  it.each(cases)(
    '%s refuses an answer with a missing or wrong required field',
    async (_, read, __, bad) => {
      answer(bad(undefined as never));
      const result = await read();
      expect(result.success).toBe(false);
      expect(result.error?.code).toBe('INTERNAL.RESPONSE_CONTRACT');
    },
  );

  it('keeps cells keyed by column key and the rule as the API wrote it', async () => {
    answer(PREVIEW);
    const preview = await api.previewSegment({ entity: 'person', rule: RULE as never });
    expect(preview.success && preview.data.sample[0]?.row?.cells).toEqual({
      full_name: 'Marco Bianchi',
      title: 'CEO',
    });
    answer(SEGMENT);
    const segment = await api.getSegment('s1');
    expect(segment.success && segment.data.rule).toEqual(RULE);
  });

  it('reads the source of an AI fill in the why answer', async () => {
    const source = { url: 'https://example.com/about', title: 'About us' };
    answer({
      ...WHY,
      rule: {
        ...WHY.rule,
        children: [{ ...CONDITION_NODE, rows: [{ ...EVIDENCE, status: 'done', source }] }],
      },
    });
    const why = await api.getWhy('s1', 'i1');
    expect(why.success && why.data.rule.children[0]?.rows[0]?.source).toEqual(source);
  });

  it('pages members by cursor, never by offset', async () => {
    answer({ members: [], next_cursor: null });
    await api.getMembers('s1', 'i9');
    const url = String(vi.mocked(globalThis.fetch).mock.calls[0]?.[0]);
    expect(url).toContain('/v1/segments/s1/members?');
    expect(new URL(url).searchParams.get('after')).toBe('i9');
    expect(new URL(url).searchParams.get('limit')).toBe('50');
  });

  it('offers the columns of a list but its score column, which the fit condition reads', async () => {
    answer({
      columns: [
        { key: 'full_name', label: 'Name', kind: 'static', position: 0 },
        { key: 'fit', label: 'Fit', kind: 'score', position: 1 },
        { key: 'ai_summary', label: 'Summary', kind: 'ai', position: 2 },
      ],
    });
    expect(await api.getListColumns(LIST)).toEqual(
      succeed([
        { key: 'full_name', label: 'Name' },
        { key: 'ai_summary', label: 'Summary' },
      ]),
    );
  });
});
