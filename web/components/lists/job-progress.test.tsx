// @vitest-environment happy-dom
import { NextIntlClientProvider } from 'next-intl';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { FORMATS } from '@/i18n/config';
import type { ChatStatus } from '@/lib/chat/chat-view';
import type { Job } from '@/lib/lists/api';
import { succeed } from '@/lib/types';
import { MESSAGES } from '@/messages';
import { JobProgress } from './job-progress';

const { job } = vi.hoisted(() => ({ job: vi.fn() }));
vi.mock('@/lib/lists/api', () => ({
  listsApi: { job },
  TERMINAL_JOB: new Set(['needs_input', 'succeeded', 'failed', 'cancelled']),
}));

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const snapshot = (status: Job['status']): Job => ({
  id: 'j1',
  chat_session_id: 'c1',
  status,
  phase: null,
  progress: { done: 2, total: 0 },
  list_id: 'l1',
  credits: { reserved: 0, spent: 0 },
  stages: null,
  error: null,
  created_at: '2026-10-22T08:00:00Z',
  finished_at: null,
});

async function render(status: Job['status'], chatStatus: ChatStatus | null, streaming = false) {
  job.mockResolvedValue(succeed(snapshot(status)));
  const container = document.createElement('div');
  document.body.replaceChildren(container);
  await act(async () => {
    createRoot(container).render(
      <NextIntlClientProvider locale="it" messages={MESSAGES.it} formats={FORMATS}>
        <JobProgress job={{ id: 'j1', progress: null, streaming }} chatStatus={chatStatus} />
      </NextIntlClientProvider>,
    );
  });
  return container;
}

beforeEach(() => vi.clearAllMocks());

describe('JobProgress', () => {
  it('reads a finished turn whose step failed as the chat header does, in red, not «Completato»', async () => {
    const box = await render('succeeded', 'step_error');
    const status = box.querySelector('span');
    expect(status?.textContent).toBe('Errore in un passo');
    expect(status?.className).toContain('text-negative');
    expect(box.textContent).not.toContain('Completato');
    expect(box.textContent).toContain('2 passaggi');
  });

  it('reads a turn whose searches all failed as failed', async () => {
    const box = await render('succeeded', 'all_failed');
    expect(box.querySelector('span')?.textContent).toBe('Ricerca non riuscita');
  });

  it('keeps «Completato» for a clean turn and «In corso» while the turn still runs', async () => {
    expect((await render('succeeded', 'done')).querySelector('span')?.textContent).toBe(
      'Completato',
    );
    const live = await render('running', 'step_error', true);
    expect(live.querySelector('span')?.textContent).toBe('In corso');
  });
});
