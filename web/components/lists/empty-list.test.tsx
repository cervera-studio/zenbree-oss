// @vitest-environment happy-dom
import { NextIntlClientProvider } from 'next-intl';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { FORMATS } from '@/i18n/config';
import type { ListDetail } from '@/lib/lists/api';
import { succeed } from '@/lib/types';
import { MESSAGES } from '@/messages';
import { ListPage } from './list-page';
import { ListPanel } from './list-panel';

const { get, all, rows, fetchAllRows } = vi.hoisted(() => ({
  get: vi.fn(),
  all: vi.fn(),
  rows: vi.fn(),
  fetchAllRows: vi.fn(),
}));
vi.mock('@/lib/lists/api', () => ({
  listsApi: { get, all, rows },
  fetchAllRows,
  TERMINAL_JOB: new Set(['needs_input', 'succeeded', 'failed', 'cancelled']),
}));
vi.mock('@/lib/lists/fill-stream', () => ({ useFillStream: () => undefined }));
vi.mock('@/lib/projects', () => ({ useCurrentProjectId: () => 'p1' }));
vi.mock('next/navigation', () => ({ notFound: vi.fn() }));
vi.mock('server-only', () => ({}));

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const list = (over: Partial<ListDetail>): ListDetail => ({
  id: 'l1',
  name: 'Studi dentistici Torino',
  entity: 'person',
  mode: 'build',
  chat_session_id: 'c1',
  project_id: 'p1',
  created_at: '2026-10-22T08:00:00Z',
  row_count: 0,
  can_expand: false,
  findable: null,
  columns: [],
  status: 'empty',
  delivered_count: 0,
  target_rows: null,
  credits_used: 0,
  brief: null,
  chat_title: 'Titolari di studi dentistici a Torino',
  prices: { search: 0, email: 1, phone: 8, ai_cell: 1, ai_cells_per_run: 200 },
  ...over,
});

async function mount(node: React.ReactNode) {
  const container = document.createElement('div');
  document.body.replaceChildren(container);
  await act(async () => {
    createRoot(container).render(
      <NextIntlClientProvider locale="it" messages={MESSAGES.it} formats={FORMATS}>
        {node}
      </NextIntlClientProvider>,
    );
  });
  return container;
}

const texts = (box: HTMLElement, selector: string) =>
  [...box.querySelectorAll(selector)].map((el) => el.textContent?.trim());

const it_ = MESSAGES.it.lists;

beforeEach(() => {
  vi.clearAllMocks();
  all.mockResolvedValue(succeed({ lists: [] }));
  rows.mockResolvedValue(succeed({ rows: [], next_cursor: null }));
  fetchAllRows.mockResolvedValue(succeed([]));
});

describe('a list whose search found no one', () => {
  it('explains on the list page what to do, links back to the chat and offers no action on nothing', async () => {
    get.mockResolvedValue(succeed(list({})));
    const box = await mount(<ListPage listId="l1" balance={25} />);
    expect(box.textContent).toContain(it_.emptyListBody);
    expect(box.textContent).not.toContain(it_.noRows);
    const retry = [...box.querySelectorAll('a')].find(
      (a) => a.textContent?.trim() === it_.emptyListRetry,
    );
    expect(retry?.getAttribute('href')).toBe('/?s=c1');
    for (const action of [it_.aiAdd, it_.export, it_.startCampaign]) {
      expect(texts(box, 'header button, header a')).not.toContain(action);
    }
  });

  it('keeps «Nessuna riga ancora» and the actions while the search still runs', async () => {
    get.mockResolvedValue(succeed(list({ status: 'running' })));
    const box = await mount(<ListPage listId="l1" balance={25} />);
    expect(box.textContent).toContain(it_.noRows);
    expect(box.textContent).not.toContain(it_.emptyListBody);
    expect(texts(box, 'header button, header a')).toContain(it_.startCampaign);
  });

  it('explains it in the chat panel too, with no link to the chat it sits in and no actions', async () => {
    get.mockResolvedValue(succeed(list({})));
    const box = await mount(
      <ListPanel listId="l1" chatStatus="done" balance={25} meta={() => null} />,
    );
    expect(box.textContent).toContain(it_.emptyListBody);
    expect(box.textContent).not.toContain(it_.emptyListRetry);
    expect(texts(box, 'button')).not.toContain(it_.aiAdd);
    expect(texts(box, 'button')).not.toContain(it_.export);
    expect(box.textContent).not.toContain(it_.startCampaign);
  });

  it('keeps «Altre righe» on the page when the search can still go on', async () => {
    get.mockResolvedValue(succeed(list({ can_expand: true })));
    const box = await mount(<ListPage listId="l1" balance={25} />);
    expect(box.textContent).toContain(it_.emptyListBody);
    expect(texts(box, 'header button')).toContain(it_.moreRows);
  });

  it('says nothing about a search for a CSV import that added no row', async () => {
    get.mockResolvedValue(succeed(list({ mode: 'enrich' })));
    const box = await mount(<ListPage listId="l1" balance={25} />);
    expect(box.textContent).toContain(it_.noRows);
    expect(box.textContent).not.toContain(it_.emptyListBody);
  });

  it('keeps «Nessuna riga ancora» in the panel while a new turn runs on a list that was empty', async () => {
    get.mockResolvedValue(succeed(list({})));
    const box = await mount(
      <ListPanel listId="l1" chatStatus="running" balance={25} meta={() => null} />,
    );
    expect(box.textContent).toContain(it_.noRows);
    expect(box.textContent).not.toContain(it_.emptyListBody);
  });
});
