import { describe, expect, it } from 'vite-plus/test';
import { hydrateMessages } from './session-loader';
import type { ChatMessage } from './types';

const row = (
  toolCalls: ChatMessage['toolCalls'],
  extra: Partial<ChatMessage> = {},
): ChatMessage => ({
  id: 'm',
  sessionId: 's',
  role: 'assistant',
  content: '',
  toolCalls,
  creditsUsed: 0,
  createdAt: '2026-10-05T00:00:00Z',
  sources: null,
  status: null,
  terminalCode: null,
  question: null,
  ...extra,
});

describe('hydrateMessages', () => {
  it('keeps the error code of a failed call, so a reload reads like the live turn (#735)', () => {
    const [msg] = hydrateMessages([
      row([
        {
          id: 'e',
          name: 'search_companies',
          input: {},
          verdict: 'failed',
          errorCode: 'no_credits',
        },
      ]),
    ]);
    expect(msg?.toolCalls[0]?.errorCode).toBe('no_credits');
  });

  it('reads the typed fields of a saved call, and none from a shape it cannot read', () => {
    const brief = { who: ['CEO'], companies: [], where: [], signal: [], email: null };
    const campaign = { id: 'c1', name: 'CEO Milano', targetCount: 47 };
    const summary = { kind: 'legacy', contact: 'email', found: 4, failed: 0, refunded: 0 };
    const [msg] = hydrateMessages([
      row([
        { id: 'b', name: 'set_brief', input: {}, verdict: 'success', brief },
        { id: 'r', name: 'remember', input: {}, verdict: 'success', memorySaved: 'Vende HR' },
        { id: 'd', name: 'draft_campaign', input: {}, verdict: 'success', campaign },
        { id: 'e', name: 'enrich_email', input: {}, verdict: 'success', summary },
        { id: 'x', name: 'set_brief', input: {}, verdict: 'success', brief: 'garbled' },
        { id: 'y', name: 'enrich_email', input: {}, verdict: 'success', summary: { count: 2 } },
      ]),
    ]);
    expect(msg?.toolCalls.map((c) => [c.brief, c.memorySaved, c.campaign, c.summary])).toEqual([
      [brief, undefined, undefined, undefined],
      [undefined, 'Vende HR', undefined, undefined],
      [undefined, undefined, campaign, undefined],
      [undefined, undefined, undefined, summary],
      [undefined, undefined, undefined, undefined],
      [undefined, undefined, undefined, undefined],
    ]);
  });

  it('marks a saved failed turn, which has no text to show', () => {
    const [failed, ok] = hydrateMessages([
      row([], { status: 'failed' }),
      row([], { status: 'success' }),
    ]);
    expect(failed).toMatchObject({ status: 'failed', turnFailed: true, parts: [] });
    expect(ok?.turnFailed).toBeUndefined();
  });
});
