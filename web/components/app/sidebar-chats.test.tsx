import { NextIntlClientProvider } from 'next-intl';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vite-plus/test';
import { FORMATS } from '@/i18n/config';
import type { NavSession } from '@/lib/home/nav';
import { MESSAGES } from '@/messages';
import { RecentChats } from './sidebar-chats';

const live = vi.hoisted(() => ({ sessions: [] as NavSession[] | null, failed: false }));

vi.mock('next/navigation', () => ({
  usePathname: () => '/',
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/lib/home/nav', () => ({
  useNavState: () => ({ nav: live.sessions && { sessions: live.sessions }, failed: live.failed }),
}));
vi.mock('@/lib/chat/stream-provider', () => ({ useChatStreamStore: () => ({}) }));
vi.mock('@/components/projects/project-switcher', () => ({ ProjectSwitcher: () => null }));

const base = {
  id: 's',
  title: 'Agenzie a Milano',
  updatedAt: '2026-10-08T09:00:00Z',
  pinnedAt: null,
  progressDone: 0,
  progressTotal: 0,
  listCount: 0,
  people: 0,
  companies: 0,
};

function render(phone = false) {
  return renderToStaticMarkup(
    <NextIntlClientProvider locale="it" messages={MESSAGES.it} formats={FORMATS}>
      <RecentChats phone={phone} />
    </NextIntlClientProvider>,
  );
}

function meta(session: Partial<NavSession>) {
  live.failed = false;
  live.sessions = [{ ...base, status: null, ...session }];
  const html = render();
  const link = html.match(/<a [^>]*>(.*?)<\/a>/)?.[1] ?? '';
  return {
    text: link
      .replace(/<[^>]+>/g, ' ')
      .replace(/\s+/g, ' ')
      .trim(),
    dot: link.match(/rounded-full ([\w-]+)/)?.[1],
  };
}

describe('chat status in the sidebar', () => {
  it.each([
    [{ status: null }, 'Agenzie a Milano', undefined],
    [
      { status: 'running', progressDone: 64, progressTotal: 1200 },
      'Agenzie a Milano In corso · 64 di 1.200',
      'bg-accent',
    ],
    [{ status: 'error' }, 'Agenzie a Milano Errore in un passo', 'bg-negative'],
    [{ status: 'asking' }, 'Agenzie a Milano Aspetta una tua risposta', 'bg-warning'],
    [{ status: 'plan' }, 'Agenzie a Milano Piano da approvare', 'bg-accent'],
    [{ status: 'done' }, 'Agenzie a Milano Risposta pronta', 'bg-positive'],
    [
      { status: 'done', listCount: 2, people: 47, companies: 1031 },
      'Agenzie a Milano 2 liste · 47 persone, 1.031 aziende',
      'bg-positive',
    ],
  ] as const)('%j reads %j', (session, text, dot) => {
    expect(meta(session)).toEqual({ text, dot });
  });

  it('is empty-state text with no chats', () => {
    live.sessions = [];
    expect(render()).toContain('Ancora nessuna conversazione.');
  });

  it('says the chats could not load while the stream is refused before its first frame', () => {
    live.sessions = null;
    live.failed = true;
    expect(render()).toContain('Impossibile caricare le conversazioni.');
    live.sessions = [{ ...base, status: null }];
    expect(render()).not.toContain('Impossibile caricare');
  });

  it('keeps a 48 px target on the phone, status or not', () => {
    live.failed = false;
    live.sessions = [{ ...base, status: null }];
    expect(render(true)).toMatch(/<a [^>]*class="[^"]*min-h-12/);
  });
});

describe('recent chats', () => {
  it('shows the newest eight chats and no link to the rest, which the Chat page reaches', () => {
    live.failed = false;
    live.sessions = Array.from({ length: 10 }, (_, i) => ({
      ...base,
      id: `s${i}`,
      title: `Chat ${i}`,
      status: null,
    }));
    const html = render();
    expect(html).toContain('Chat 7');
    expect(html).not.toContain('Chat 8');
    expect(html).not.toContain('href="/chats"');
  });
});
