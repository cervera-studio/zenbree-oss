// @vitest-environment happy-dom
import { act } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { click, control, render } from '@/app/auth/testing';
import type { NavSession } from '@/lib/home/nav';
import { AllChats } from './all-chats';

const live = vi.hoisted(() => ({ sessions: [] as NavSession[] | null, failed: false }));
const api = vi.hoisted(() => ({
  pin: vi.fn(async () => ({ success: true })),
  remove: vi.fn(async () => ({ success: true })),
}));

vi.mock('next/navigation', () => ({
  usePathname: () => '/chats',
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/lib/home/nav', () => ({
  useNavState: () => ({ nav: live.sessions && { sessions: live.sessions }, failed: live.failed }),
}));
vi.mock('@/lib/chat/sessions', () => ({ sessionsApi: api }));
const toastError = vi.hoisted(() => vi.fn());
vi.mock('@/components/ui/sonner', () => ({ toast: { error: toastError, success: vi.fn() } }));
vi.mock('@/lib/chat/stream-provider', () => ({ useChatStreamStore: () => ({}) }));

const chat = (id: string, title: string | null, pinnedAt: string | null = null): NavSession => ({
  id,
  title,
  updatedAt: '2026-10-08T09:00:00Z',
  pinnedAt,
  status: null,
  progressDone: 0,
  progressTotal: 0,
  listCount: 0,
  people: 0,
  companies: 0,
});

const titles = (c: HTMLElement) =>
  [...c.querySelectorAll('li a')].map((a) => a.textContent?.trim());

async function search(c: HTMLElement, value: string) {
  const input = c.querySelector('input[type="search"]') as HTMLInputElement;
  const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  await act(async () => {
    setValue?.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

beforeEach(() => {
  live.failed = false;
  live.sessions = [
    chat('a', 'Agenzie marketing Milano', '2026-10-07T09:00:00Z'),
    chat('b', 'CEO SaaS a Torino'),
    chat('c', null),
  ];
  api.pin.mockClear();
  api.remove.mockClear();
});

describe('all chats', () => {
  it('lists every chat in the stream order and filters by title, ignoring case', async () => {
    const c = await render(<AllChats />);
    expect(titles(c)).toEqual(['Agenzie marketing Milano', 'CEO SaaS a Torino', 'Senza titolo']);
    await search(c, '  saas ');
    expect(titles(c)).toEqual(['CEO SaaS a Torino']);
    await search(c, 'roma');
    expect(c.textContent).toContain('Nessuna chat per «roma».');
  });

  it('pins and unpins through the API, and shows which are pinned', async () => {
    const c = await render(<AllChats />);
    const pinned = control(c, 'Fissa «Agenzie marketing Milano»');
    expect(pinned.getAttribute('aria-pressed')).toBe('true');
    await click(pinned);
    await click(control(c, 'Fissa «CEO SaaS a Torino»'));
    expect(api.pin.mock.calls).toEqual([
      ['a', false],
      ['b', true],
    ]);
  });

  it('deletes only after the confirm step', async () => {
    const c = await render(<AllChats />);
    await click(control(c, 'Elimina «CEO SaaS a Torino»'));
    expect(api.remove).not.toHaveBeenCalled();
    await click(control(document.body, 'Elimina'));
    expect(api.remove).toHaveBeenCalledExactlyOnceWith('b');
  });

  it('cannot delete a chat that is answering, and says why on click', async () => {
    live.sessions = [{ ...chat('r', 'In corso'), status: 'running' }];
    const c = await render(<AllChats />);
    const button = control(c, 'Elimina «In corso»');
    expect(button.getAttribute('aria-disabled')).toBe('true');
    await click(button);
    expect(toastError).toHaveBeenCalledExactlyOnceWith(
      'Aspetta che la chat finisca di rispondere prima di eliminarla.',
    );
    expect(document.querySelector('[role="alertdialog"]')).toBeNull();
    expect(api.remove).not.toHaveBeenCalled();
  });

  it('says so with no chats, and when the stream is refused before its first frame', async () => {
    live.sessions = [];
    expect((await render(<AllChats />)).textContent).toContain('Ancora nessuna conversazione.');
    live.sessions = null;
    live.failed = true;
    expect((await render(<AllChats />)).textContent).toContain(
      'Impossibile caricare le conversazioni.',
    );
  });
});
