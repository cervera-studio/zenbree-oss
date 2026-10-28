import { describe, expect, it } from 'vite-plus/test';
import { type Brief, briefStage, latestBrief, markExcluded, parseBrief } from './brief';
import type { UIMessage, UIToolCall } from './stream-store-types';

const brief = (who: string[]): Brief => ({
  who,
  companies: [],
  where: [],
  signal: [],
  email: null,
});

const briefCall = (parsed: Brief | undefined, id = 'b'): UIToolCall => ({
  id,
  name: 'set_brief',
  input: {},
  status: 'done',
  brief: parsed,
});

const msg = (toolCalls: UIToolCall[], extra: Partial<UIMessage> = {}): UIMessage => ({
  id: 'm',
  role: 'assistant',
  content: '',
  toolCalls,
  parts: [],
  createdAt: new Date(0),
  ...extra,
});

describe('parseBrief', () => {
  it('reads the typed brief of a tool result', () => {
    expect(
      parseBrief({
        who: ['CEO', 3],
        companies: ['Agenzia'],
        where: [],
        signal: [],
        email: 'required',
      }),
    ).toEqual({ who: ['CEO'], companies: ['Agenzia'], where: [], signal: [], email: 'required' });
  });
});

describe('latestBrief', () => {
  it('is the latest readable brief of the chat, else null', () => {
    expect(
      latestBrief([
        msg([briefCall(brief(['CEO']), 'a')]),
        msg([briefCall(brief(['CEO', 'Founder']), 'b')]),
        msg([briefCall(undefined, 'c')]),
      ]),
    ).toEqual(brief(['CEO', 'Founder']));
    expect(latestBrief([msg([])])).toBeNull();
  });
});

describe('briefStage', () => {
  const lists = {
    company: { id: 'c', entity: 'company', name: null, rowCount: 31, version: 1 },
  } as const;
  const plan = msg([], { question: { question: '', options: [], plan: {} as never } });

  it.each([
    ['needs_answer', [], {}, 'brief'],
    ['plan_ready', [plan], {}, 'plan'],
    ['running', [plan], {}, 'search'],
    ['running', [], lists, 'search'],
    ['running', [], {}, 'brief'],
    ['done', [], lists, 'list'],
    ['done', [], {}, 'brief'],
  ] as const)('%s with %j is %s', (status, messages, chatLists, stage) => {
    expect(briefStage(status, messages, chatLists)).toBe(stage);
  });
});

describe('markExcluded', () => {
  const funnel = (group: 'where' | 'size' | 'who', excludedIndex = 1) => ({
    steps: [
      { group: 'what' as const, values: [], count: 40 },
      { group, values: [], count: 0 },
    ],
    excludedIndex,
    entity: 'company' as const,
  });

  it('names the brief field holding the filter that left 0', () => {
    expect(markExcluded(brief(['CEO']), funnel('where'))?.excluded).toBe('where');
    expect(markExcluded(brief(['CEO']), funnel('size'))?.excluded).toBe('companies');
    expect(markExcluded(brief(['CEO']), funnel('who'))?.excluded).toBe('who');
  });

  it('leaves the brief alone without a funnel, or with an index past its steps', () => {
    expect(markExcluded(brief(['CEO']), undefined)).toEqual(brief(['CEO']));
    expect(markExcluded(brief(['CEO']), funnel('where', 5))).toEqual(brief(['CEO']));
    expect(markExcluded(null, funnel('where'))).toBeNull();
  });
});
