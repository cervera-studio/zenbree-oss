import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vite-plus/test';
import { TestIntl } from '@/app/auth/testing';
import type { ChatView } from '@/lib/chat/chat-view';
import { ChatHeader } from './chat-header';

const list = (entity: 'company' | 'person', rowCount: number | null, id = entity) => ({
  id,
  entity,
  name: null,
  rowCount,
  version: 0,
});

function render(view: Partial<ChatView>, openEntity: 'company' | 'person' | null = null) {
  const full: ChatView = {
    title: 'Agenzie a Milano',
    status: 'done',
    creditsUsed: 12,
    chips: [],
    draftListId: null,
    brief: null,
    stage: 'brief',
    ...view,
  };
  return renderToStaticMarkup(
    <TestIntl>
      <ChatHeader view={full} openEntity={openEntity} onToggleList={() => {}} />
    </TestIntl>,
  );
}

describe('ChatHeader', () => {
  it('shows the title as the page heading', () => {
    expect(render({})).toMatch(/<h1[^>]*>Agenzie a Milano<\/h1>/);
    expect(render({ title: null })).toContain('Senza titolo');
  });

  it.each([
    ['running', 'In corso'],
    ['plan_ready', 'Piano da approvare'],
    ['needs_answer', 'Serve una risposta'],
    ['step_error', 'Errore in un passo'],
    ['all_failed', 'Ricerca non riuscita'],
    ['done', 'Finita'],
  ] as const)('labels the %s status in a live region', (status, label) => {
    expect(render({ status })).toMatch(
      new RegExp(`role="status"[^>]*>(<span[^>]*>)*(<span[^>]*></span>)?${label}`),
    );
  });

  it('renders no status and no credits before the first message, then the credits used', () => {
    const html = render({ status: null, creditsUsed: 0 });
    expect(html).not.toContain('role="status"');
    expect(html).not.toContain('crediti usati');
    expect(render({ creditsUsed: 12 })).toContain('12 crediti usati');
    expect(render({ creditsUsed: 1 })).toContain('1 credito usato');
    expect(render({ creditsUsed: 0 })).toContain('0 crediti usati');
  });

  it('names the chip group and shows a pressed chip for the open list', () => {
    const chips: ChatView['chips'] = [
      { kind: 'rows', list: list('company', 31), count: 31 },
      { kind: 'rows', list: list('person', 12), count: 12 },
    ];
    const html = render({ chips }, 'person');
    expect(html).toContain('aria-label="Liste di questa chat"');
    const buttons = [
      ...html.matchAll(/<button[^>]*aria-pressed="(true|false)"[^>]*>(.*?)<\/button>/g),
    ];
    expect(buttons.map((b) => [b[1], b[2]?.replace(/<[^>]+>/g, '')])).toEqual([
      ['false', 'Aziende 31'],
      ['true', 'Persone 12'],
    ]);
    expect(render({})).not.toContain('Liste di questa chat');
  });

  it('renders the pending plan list and an empty list as non-interactive chips', () => {
    const chips: ChatView['chips'] = [
      { kind: 'rows', list: list('company', 31), count: 31 },
      { kind: 'draft', list: list('person', 0) },
    ];
    const html = render({ chips, status: 'plan_ready', draftListId: 'person' });
    expect(html).toContain('Persone');
    expect(html).toContain('in bozza');
    expect(html.match(/<button/g)).toHaveLength(1);
    const empty = render({ chips: [{ kind: 'empty', list: list('person', 0) }] });
    expect(empty).not.toContain('<button');
    expect(empty).toMatch(/Persone.*0/);
  });
});
