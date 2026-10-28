import fc from 'fast-check';
import { describe, expect, it } from 'vite-plus/test';
import type { ChatList, ChatLists } from './chat-lists';
import { type ChatTurn, IDLE } from './chat-turn';
import { deriveChatView, listMeta, resolvePanel, sameView } from './chat-view';
import type { JobProgress } from './job-progress';
import type { UIMessage, UIToolCall } from './stream-store-types';
import type { AgentPlan } from './types';

let n = 0;
function msg(role: UIMessage['role'], extra: Partial<UIMessage> = {}): UIMessage {
  return {
    id: `m${++n}`,
    role,
    content: role === 'user' ? 'agenzie a Milano' : 'ok',
    toolCalls: [],
    parts: [],
    createdAt: new Date(0),
    ...extra,
  };
}

function list(
  entity: ChatList['entity'],
  rowCount: number | null,
  id = entity[0] ?? 'x',
): ChatList {
  return { id, entity, name: null, rowCount, version: 0 };
}

const plan = { listId: 'p', entity: 'person' } as AgentPlan;
const STREAMING: ChatTurn = { phase: 'streaming', jobId: 'j' };

function view(
  messages: UIMessage[],
  extra: {
    turn?: ChatTurn;
    lists?: ChatLists;
    title?: string | null;
    progress?: JobProgress | null;
    fillCredits?: number;
  } = {},
) {
  return deriveChatView({
    messages,
    sessionId: 's',
    turn: IDLE,
    lists: {},
    title: null,
    progress: null,
    fillCredits: 0,
    ...extra,
  });
}

const failedProgress: JobProgress = {
  stages: [{ tool: 't', label: 'L', units: 5, done: 1, error: { code: 'provider' } }],
  phase: null,
  creditsSpent: 0,
  retryStage: null,
  listId: null,
};

const errorCall = { id: 't', name: 'x', input: {}, status: 'error' } satisfies UIToolCall;
const searchError = { ...errorCall, name: 'search_companies' } satisfies UIToolCall;

describe('status', () => {
  const user = msg('user');
  it.each([
    [
      'a streaming turn beats a stale question',
      [msg('user'), msg('assistant', { question: { question: 'q', options: [] } })],
      { turn: STREAMING },
      'running',
    ],
    ['a trailing user row is a reattach in flight', [user], {}, 'running'],
    [
      'a cut turn may still run server-side',
      [user, msg('assistant', { interrupted: 'partial' })],
      {},
      'running',
    ],
    ['a turn stopped with Ferma before it was saved', [user, msg('assistant')], {}, 'running'],
    [
      'a reattach that found no live job',
      [user, msg('assistant', { interrupted: 'resync' })],
      {},
      'done',
    ],
    [
      'a plan awaiting approval',
      [user, msg('assistant', { question: { question: '', options: [], plan } })],
      {},
      'plan_ready',
    ],
    [
      'an ask_user question',
      [
        user,
        msg('assistant', {
          question: { question: 'q', options: [{ label: 'a', description: null }] },
        }),
      ],
      {},
      'needs_answer',
    ],
    [
      'every step is a search that failed',
      [user, msg('assistant', { status: 'success', toolCalls: [searchError] })],
      {},
      'all_failed',
    ],
    [
      'a lone failed enrichment is not a failed search',
      [
        user,
        msg('assistant', {
          status: 'success',
          toolCalls: [{ ...errorCall, name: 'enrich_email' }],
        }),
      ],
      {},
      'step_error',
    ],
    [
      'a refused search (no credits) keeps its own wording',
      [
        user,
        msg('assistant', {
          status: 'success',
          toolCalls: [{ ...searchError, errorCode: 'no_credits' }],
        }),
      ],
      {},
      'step_error',
    ],
    [
      'one failed tool next to a good one',
      [
        user,
        msg('assistant', {
          status: 'success',
          toolCalls: [searchError, { ...searchError, id: 'ok', status: 'done' }],
        }),
      ],
      {},
      'step_error',
    ],
    [
      'a failed hidden tool is not a failed step',
      [
        user,
        msg('assistant', {
          status: 'success',
          toolCalls: [
            { ...errorCall, id: 'e1', name: 'set_brief' },
            { ...errorCall, id: 'e2', name: 'ask_user' },
          ],
        }),
      ],
      {},
      'done',
    ],
    ['a failed turn', [user, msg('assistant', { status: 'failed' })], {}, 'step_error'],
    [
      'a refused plan approval leaves the plan to approve (#260)',
      [
        user,
        msg('assistant', { question: { question: '', options: [], plan } }),
        msg('assistant', { status: 'failed', content: 'Un passo è obbligatorio' }),
      ],
      {},
      'plan_ready',
    ],
    [
      'a budget stop is not an error',
      [user, msg('assistant', { status: 'partial', terminalCode: 'max_turns_reached' })],
      {},
      'done',
    ],
    [
      'a dropped stream reads as offline, not as running',
      [user, msg('assistant', { interrupted: 'partial' })],
      { turn: { phase: 'lost', jobId: null } },
      'offline',
    ],
    [
      'a stage that failed in an answered turn',
      [user, msg('assistant', { status: 'success' })],
      { progress: failedProgress },
      'step_error',
    ],
    [
      'a retry in flight shows running even while the old error is still on the card',
      [user, msg('assistant')],
      { turn: STREAMING, progress: failedProgress },
      'running',
    ],
    [
      'a failed step three turns ago is history',
      [
        user,
        msg('assistant', { status: 'success', toolCalls: [errorCall] }),
        msg('user'),
        msg('assistant', { status: 'success' }),
      ],
      {},
      'done',
    ],
  ] as const)('%s', (_, messages, extra, expected) => {
    expect(view([...messages], extra)?.status).toBe(expected);
  });

  it('has no status and no header on an empty draft', () => {
    expect(view([])?.status).toBeNull();
    expect(
      deriveChatView({
        messages: [],
        sessionId: null,
        turn: IDLE,
        lists: {},
        title: null,
        progress: null,
        fillCredits: 0,
      }),
    ).toBeNull();
  });
});

const EDGE = `${'a'.repeat(40)} ${'b'.repeat(38)} cc`;

function titleOf(content: string): string {
  return view([msg('user', { content })])?.title ?? '';
}

const word = fc.string({
  unit: fc.constantFrom('a', 'b', '1', 'é', '😀'),
  minLength: 1,
  maxLength: 90,
});
const gap = fc.constantFrom(' ', '  ', '\t', '\u00a0', '\u3000');
const lineBreak = fc.constantFrom('\n', '\r', '\r\n', '\v', '\f', '\x85', '\u2028', '\u2029');
/** A message and its first non-empty line with whitespace collapsed, known from how it was built. */
const message = fc
  .array(fc.array(fc.tuple(word, gap), { maxLength: 8 }), { maxLength: 4 })
  .chain((lines) =>
    fc.tuple(gap, lineBreak).map(([lead, br]) => ({
      text: lines.map((words) => lead + words.map(([w, g]) => w + g).join('')).join(br),
      line: (lines.find((words) => words.length) ?? []).map(([w]) => w).join(' '),
    })),
  );

describe('credits and title', () => {
  it('nets refunds and ignores user rows', () => {
    const v = view([
      msg('user'),
      msg('assistant', { creditsUsed: 10 }),
      msg('user'),
      msg('assistant', { creditsUsed: -2 }),
    ]);
    expect(v?.creditsUsed).toBe(8);
  });

  it('adds the AI column fills, billed outside the turns (#239)', () => {
    expect(
      view([msg('user'), msg('assistant', { creditsUsed: 10 })], { fillCredits: 31 })?.creditsUsed,
    ).toBe(41);
  });

  it('prefers the server title, else the first line of the first user message cut at a word', () => {
    expect(view([msg('user')], { title: 'Renamed' })?.title).toBe('Renamed');
    expect(view([msg('user', { content: `  ${'a'.repeat(100)}` })])?.title).toBe(
      `${'a'.repeat(79)}…`,
    );
    expect(view([msg('user', { content: `Cerca ${'x'.repeat(100)}` })])?.title).toBe(
      `Cerca ${'x'.repeat(73)}…`,
    );
    // the API's cut, never "tra 50 e 20" for "tra 50 e 200"
    const brief =
      'Trova i responsabili acquisti di aziende manifatturiere in Lombardia tra 50 e 200 dipendenti';
    expect(view([msg('user', { content: brief })])?.title).toBe(
      'Trova i responsabili acquisti di aziende manifatturiere in Lombardia tra 50 e…',
    );
    expect(view([msg('user', { content: '\n Build   my list\n- titles: CTO' })])?.title).toBe(
      'Build my list',
    );
    expect(view([msg('assistant')])?.title).toBeNull();
  });

  // the same inputs and titles as api/tests/test_chat_title.py
  it('cuts like the API: by code point, on the same line breaks, keeping a word that ends at 79', () => {
    expect(titleOf(EDGE)).toBe(`${'a'.repeat(40)} ${'b'.repeat(38)}…`);
    expect(titleOf(`${'x'.repeat(78)}😀😀`)).toBe(`${'x'.repeat(78)}😀😀`);
    expect(titleOf(`${'x'.repeat(78)}😀😀😀`)).toBe(`${'x'.repeat(78)}😀…`);
    expect(titleOf(' \r first line\rsecond')).toBe('first line');
    // found by fast-check
    expect(titleOf(' 😀1béb1a😀b 😀aaba  b \r aé\t\r ')).toBe('😀1béb1a😀b 😀aaba b');
  });

  it('is the line or its longest cut that fits', () => {
    fc.assert(
      fc.property(message, ({ text, line }) => {
        const title = [...titleOf(text)];
        const chars = [...line];
        expect(title.length).toBeLessThanOrEqual(80);
        if (chars.length <= 80) {
          expect(title.join('')).toBe(line);
          return;
        }
        expect(title.at(-1)).toBe('…');
        const head = title.slice(0, -1);
        expect(chars.slice(0, head.length)).toEqual(head);
        // a word cut: the next character is a space and no later word still fits
        if (head.length !== 79) {
          expect(chars[head.length]).toBe(' ');
          expect(head.length).toBeGreaterThanOrEqual(40);
          expect(chars.slice(head.length + 1, 80)).not.toContain(' ');
        }
      }),
      { examples: [[{ text: EDGE, line: EDGE }]] },
    );
  });

  it('is already a title', () => {
    fc.assert(
      fc.property(fc.string({ unit: 'binary', maxLength: 200 }), (text) => {
        const title = titleOf(text);
        expect([...title].length).toBeLessThanOrEqual(80);
        expect(titleOf(title)).toBe(title);
      }),
    );
  });
});

describe('chips', () => {
  it('orders Aziende before Persone and marks only the pending plan list as a draft', () => {
    const lists = { person: list('person', 0, 'p'), company: list('company', 31) };
    const planned = [
      msg('user'),
      msg('assistant', { question: { question: '', options: [], plan } }),
    ];
    expect(view(planned, { lists })?.chips.map((c) => [c.list.entity, c.kind])).toEqual([
      ['company', 'rows'],
      ['person', 'draft'],
    ]);
    const answered = [...planned, msg('user'), msg('assistant')];
    expect(view(answered, { lists })?.chips.map((c) => c.kind)).toEqual(['rows', 'empty']);
  });
});

describe('resolvePanel', () => {
  const both = { person: list('person', 6), company: list('company', 4) };
  it('auto follows the list the live turn writes', () => {
    expect(resolvePanel({ kind: 'auto' }, both, 'company', 'running')).toBe('company');
  });
  it('auto opens the first list with rows on a reload', () => {
    expect(resolvePanel({ kind: 'auto' }, both, null, 'done')).toBe('person');
    expect(resolvePanel({ kind: 'auto' }, { company: list('company', 4) }, null, 'done')).toBe(
      'company',
    );
  });
  it('auto stays shut on the plan and question screens', () => {
    expect(resolvePanel({ kind: 'auto' }, both, null, 'plan_ready')).toBeNull();
    expect(resolvePanel({ kind: 'auto' }, both, null, 'needs_answer')).toBeNull();
  });
  it('an explicit open wins and falls back when its list does not exist', () => {
    expect(resolvePanel({ kind: 'open', entity: 'company' }, both, 'person', 'running')).toBe(
      'company',
    );
    expect(
      resolvePanel(
        { kind: 'open', entity: 'company' },
        { person: list('person', 6) },
        null,
        'done',
      ),
    ).toBe('person');
    expect(
      resolvePanel(
        { kind: 'open', entity: 'person' },
        { person: list('person', 0), company: list('company', 3) },
        'company',
        'plan_ready',
      ),
    ).toBe('person');
    expect(resolvePanel({ kind: 'open', entity: 'person' }, both, null, 'plan_ready')).toBe(
      'person',
    );
  });
  it('closed is closed', () => {
    expect(resolvePanel({ kind: 'closed' }, both, 'person', 'running')).toBeNull();
  });
  it('is null when no list has rows', () => {
    expect(
      resolvePanel({ kind: 'auto' }, { person: list('person', 0) }, 'person', 'running'),
    ).toBeNull();
  });
});

describe('listMeta', () => {
  const fetched = {
    id: 'p',
    row_count: 12,
    delivered_count: 12,
    target_rows: 38 as number | null,
  };
  const base = {
    status: 'done' as const,
    lists: { person: list('person', 12, 'p') },
    draftListId: null,
    activeEntity: null,
  };

  it('draft only for the pending plan list with no rows', () => {
    expect(listMeta({ ...fetched, row_count: 0 }, 'person', { ...base, draftListId: 'p' })).toEqual(
      { kind: 'draft' },
    );
    expect(listMeta(fetched, 'person', { ...base, draftListId: 'p' })).not.toEqual({
      kind: 'draft',
    });
  });
  it('Aziende is the base once a Persone list exists, unless it is being written', () => {
    const company = { ...fetched, id: 'c' };
    expect(listMeta(company, 'company', base)).toEqual({ kind: 'base' });
    expect(
      listMeta(company, 'company', { ...base, status: 'running', activeEntity: 'company' }).kind,
    ).toBe('running');
    expect(listMeta(company, 'company', { ...base, lists: {} }).kind).toBe('settled');
  });
  it('running says how many are left only when the target is known', () => {
    const running = { ...base, status: 'running' as const, activeEntity: 'person' as const };
    expect(listMeta(fetched, 'person', running)).toEqual({
      kind: 'running',
      delivered: 12,
      remaining: 26,
    });
    expect(listMeta({ ...fetched, target_rows: null }, 'person', running)).toEqual({
      kind: 'running',
      delivered: 12,
      remaining: null,
    });
    expect(listMeta({ ...fetched, target_rows: 5 }, 'person', running)).toMatchObject({
      remaining: 0,
    });
  });
  it('a reattached running turn (no live entity) writes Persone', () => {
    expect(listMeta(fetched, 'person', { ...base, status: 'running' }).kind).toBe('running');
  });
  it('settled reads delivered of target, else of the row count', () => {
    expect(listMeta(fetched, 'person', base)).toEqual({
      kind: 'settled',
      delivered: 12,
      total: 38,
    });
    expect(listMeta({ ...fetched, target_rows: null, delivered_count: 9 }, 'person', base)).toEqual(
      { kind: 'settled', delivered: 9, total: 12 },
    );
  });
});

describe('one credits number', () => {
  it('the header counts what the card counts when only the stages frame has reported', () => {
    expect(
      view([msg('user'), msg('assistant')], { progress: { ...failedProgress, creditsSpent: 86 } })
        ?.creditsUsed,
    ).toBe(86);
  });
});

describe('resolvePanel on a phone', () => {
  const both = { person: list('person', 6) };
  it('stays shut by itself while a run goes, so the steps card shows', () => {
    expect(resolvePanel({ kind: 'auto' }, both, 'person', 'running', true)).toBeNull();
    expect(resolvePanel({ kind: 'auto' }, both, 'person', 'running', false)).toBe('person');
    expect(resolvePanel({ kind: 'open', entity: 'person' }, both, 'person', 'running', true)).toBe(
      'person',
    );
  });
});

describe('deriveChatView brief', () => {
  const brief = { who: ['CEO'], companies: [], where: ['Bolzano'], signal: [], email: null };
  const briefTurn = msg('assistant', {
    status: 'success',
    toolCalls: [{ id: 'b', name: 'set_brief', input: {}, status: 'done', brief }],
  });
  const funnel = {
    steps: [{ group: 'where' as const, values: ['Bolzano'], count: 0 }],
    excludedIndex: 0,
    entity: 'company' as const,
  };
  const noResults = msg('assistant', { question: { question: 'q', options: [], funnel } });

  it('marks the excluding field while the chat stands on an empty search', () => {
    expect(view([msg('user'), briefTurn, noResults])?.brief?.excluded).toBe('where');
  });

  it('drops the mark once the user answered', () => {
    expect(view([msg('user'), briefTurn, noResults, msg('user')])?.brief?.excluded).toBeUndefined();
  });
});

describe('sameView', () => {
  const brief = { who: ['CEO'], companies: [], where: ['Bolzano'], signal: [], email: null };
  const funnel = {
    steps: [{ group: 'where' as const, values: ['Bolzano'], count: 0 }],
    excludedIndex: 0,
    entity: 'company' as const,
  };
  const turn = (where = brief.where) => [
    msg('user'),
    msg('assistant', {
      status: 'success',
      toolCalls: [
        { id: 'b', name: 'set_brief', input: {}, status: 'done', brief: { ...brief, where } },
      ],
    }),
    msg('assistant', { question: { question: 'q', options: [], funnel } }),
  ];
  const lists = (rows: number) => ({ company: list('company', rows) });

  it('holds across derives that rebuild the chips and the brief with the same content', () => {
    const messages = turn();
    const three = lists(3);
    const a = view(messages, { lists: three });
    const b = view(messages, { lists: three });
    expect(a?.chips).not.toBe(b?.chips);
    expect(a?.brief).not.toBe(b?.brief);
    expect(sameView(a, b)).toBe(true);
  });

  it('breaks on a status change, a chip count change and a brief field change', () => {
    const messages = turn();
    const three = lists(3);
    const base = view(messages, { lists: three });
    expect(sameView(base, view(messages, { lists: three }))).toBe(true);
    expect(sameView(base, view(messages, { lists: three, turn: STREAMING }))).toBe(false);
    expect(sameView(base, view(messages, { lists: lists(4) }))).toBe(false);
    expect(sameView(base, view(turn(['Trento']), { lists: three }))).toBe(false);
  });
});
