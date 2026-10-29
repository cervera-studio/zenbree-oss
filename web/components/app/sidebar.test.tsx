import { NextIntlClientProvider } from 'next-intl';
import type { ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { FORMATS, type Locale } from '@/i18n/config';
import { MESSAGES } from '@/messages';
import { currentNavId, navEntries } from './nav';
import { SidebarNav } from './sidebar';

const nav = vi.hoisted(() => ({
  pathname: '/',
  search: '',
  live: null as Record<string, unknown> | null,
}));

vi.mock('next/navigation', () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ push: () => {} }),
  useSearchParams: () => new URLSearchParams(nav.search),
}));
// The chat entries need the chat store and the API: their current state isn't unit-tested.
vi.mock('./sidebar-chats', async (importActual) => ({
  ...(await importActual<typeof import('./sidebar-chats')>()),
  WorkspaceSwitcher: () => null,
}));
vi.mock('@/lib/chat/stream-provider', () => ({ useChatStreamStore: () => ({}) }));
vi.mock('./notification-bell', () => ({ NotificationBell: () => null }));
vi.mock('@/lib/home/nav', () => ({
  useNav: () => nav.live,
  useNavState: () => ({ nav: nav.live, failed: false }),
}));
vi.mock('@/lib/auth-client', () => ({ signOut: async () => {}, authClient: {} }));
// The account menu's items render inline, as if the menu were open.
vi.mock('@/components/ui/dropdown-menu', () => {
  const Inline = ({ children }: { children?: ReactNode }) => <>{children}</>;
  return {
    DropdownMenu: Inline,
    DropdownMenuTrigger: Inline,
    DropdownMenuContent: Inline,
    DropdownMenuItem: Inline,
    DropdownMenuSeparator: () => null,
  };
});

const viewer = {
  id: 'u1',
  name: 'Ada Lovelace',
  email: 'ada@example.com',
  image: null as string | null,
  company: null as string | null,
  isAdmin: false,
  isOrgAdmin: true,
  orgs: [{ id: 'org-1', name: 'Ada' }],
  activeOrgId: 'org-1',
};

function render(
  pathname: string,
  isAdmin = false,
  more: Partial<typeof viewer> = {},
  phone = false,
  credits: ReactNode = null,
) {
  const [path = '/', search = ''] = pathname.split('?');
  nav.pathname = path;
  nav.search = search;
  return renderToStaticMarkup(
    <NextIntlClientProvider locale="it" messages={MESSAGES.it} formats={FORMATS}>
      <SidebarNav viewer={{ ...viewer, isAdmin, ...more }} credits={credits} phone={phone} />
    </NextIntlClientProvider>,
  );
}

const text = (html: string) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');

function currentLinks(pathname: string) {
  const html = render(pathname);
  return [...html.matchAll(/<a [^>]*aria-current="page"[^>]*>(.*?)<\/a>/g)].map((m) =>
    (m[1] ?? '').replace(/<[^>]+>/g, ''),
  );
}

describe('sidebar current page', () => {
  it.each([
    ['/', ['Panoramica']],
    ['/chats', ['Chat']],
    ['/lists', ['Liste']],
    ['/lists/7f3c', ['Liste']],
    ['/campaigns/42', ['Campagne']],
    ['/automations/42/runs/7', ['Automazioni']],
    ['/segments/42/members', ['Segmenti']],
    ['/inbox', ['Risposte']],
    ['/settings/senders', ['Impostazioni']],
    ['/billing', ['Impostazioni']],
    ['/settings', ['Impostazioni']],
    ['/settings/exclusions', ['Esclusioni']],
    ['/settings/team', ['Team e ruoli']],
    ['/settings/integrations', ['Integrazioni']],
    ['/settings/integrations/callback', ['Integrazioni']],
    // The profile row links here, but only the Settings entry is current.
    ['/settings/profile', ['Impostazioni']],
    ['/views', ['Viste salvate']],
    ['/listsx', []],
  ])('%s marks %j', (pathname, labels) => {
    expect(currentLinks(pathname)).toEqual(labels);
  });
});

describe('exactly one current entry', () => {
  const chats = [{ id: 's1', title: 'Agenzie a Milano', status: null }];

  it.each(['/', '/chats', '/lists/7f3c'])('%s, in the rail and in the phone drawer', (pathname) => {
    nav.live = { lists: 1, campaigns: 1, approvals: 0, views: 0, replies: 0, sessions: chats };
    for (const phone of [false, true]) {
      const html = render(pathname, false, {}, phone);
      expect(html).toContain('Nuova chat');
      expect(html.match(/aria-current="page"/g)).toHaveLength(1);
    }
    nav.live = null;
  });

  it('/?s=<chat>: the open chat is current, Panoramica is not', () => {
    nav.live = { lists: 1, campaigns: 1, approvals: 0, views: 0, replies: 0, sessions: chats };
    for (const phone of [false, true]) {
      const html = render('/?s=s1', false, {}, phone);
      expect(html.match(/aria-current="page"/g)).toHaveLength(1);
      const [link = ''] = html.match(/<a [^>]*aria-current="page"[^>]*>.*?<\/a>/) ?? [];
      expect(link).toContain('href="/?s=s1"');
      expect(link).toContain('Agenzie a Milano');
    }
    nav.live = null;
  });
});

describe('sidebar groups', () => {
  it('lists the main entries in the design order, under no title', () => {
    const html = text(render('/lists'));
    expect(html).toContain(
      ' Panoramica Chat Liste Viste salvate Campagne Automazioni Segmenti Risposte ',
    );
    expect(html).not.toContain('Risultati');
  });

  it('puts the settings pages under a Workspace title', () => {
    const html = render('/lists');
    expect(html).toMatch(/<h2[^>]*>Workspace<\/h2>/);
    expect(text(html)).toContain(' Workspace Esclusioni Team e ruoli Integrazioni Impostazioni ');
  });
});

describe('sidebar account line', () => {
  it('names the role of an admin and keeps the email of anyone else', () => {
    expect(text(render('/lists', true))).toContain(' Admin ');
    expect(text(render('/lists', false))).toContain('ada@example.com');
    expect(text(render('/lists', true, {}, true))).toContain('ada@example.com');
  });
});

describe('sidebar back office entry', () => {
  it('is rendered for admins only', () => {
    expect(render('/lists', true)).toContain('href="/admin"');
    expect(render('/lists', false)).not.toContain('href="/admin"');
  });
});

describe('organization switcher', () => {
  it('lists the organizations from two up', () => {
    expect(render('/lists')).not.toContain('Cambia team');
    const html = render('/lists', false, {
      orgs: [
        { id: 'org-1', name: 'Ada' },
        { id: 'org-2', name: 'Agenzia Beta' },
      ],
    });
    expect(html).toContain('Cambia team');
    expect(html).toContain('Agenzia Beta');
  });

  it('offers a new team to everyone, also with one organization (#676)', () => {
    expect(render('/lists')).toContain('Nuovo team');
    expect(render('/lists', false, { isOrgAdmin: false })).toContain('Nuovo team');
  });

  it('hides Crediti from a member of the organization', () => {
    expect(render('/lists')).toContain('href="/billing"');
    expect(render('/lists', false, { isOrgAdmin: false })).not.toContain('href="/billing"');
  });
});

const LANDING = 'https://www.zenbree.com';

describe('nav table', () => {
  beforeEach(() => vi.stubEnv('NEXT_PUBLIC_LANDING_URL', LANDING));
  afterEach(() => vi.unstubAllEnvs());

  it('matches whole path segments only', () => {
    expect(currentNavId('/campaigns')).toBe('campaigns');
    expect(currentNavId('/campaignsfoo')).toBeNull();
    expect(currentNavId('/')).toBe('home');
    expect(currentNavId('/privacy')).toBeNull();
  });

  it('shows the back office only to admins', () => {
    const hrefs = (isAdmin: boolean) => navEntries('account', isAdmin, true).map((e) => e.href);
    expect(hrefs(false)).toEqual(['/settings/profile', '/billing', `${LANDING}/en/#faq`]);
    expect(hrefs(true)).toEqual(['/settings/profile', '/billing', `${LANDING}/en/#faq`, '/admin']);
  });

  it('links help to the landing FAQ in the user language, and hides it with no landing', () => {
    const help = (locale: Locale) =>
      navEntries('account', false, true, false, locale).find((e) => e.id === 'help')?.href;
    expect(help('it')).toBe(`${LANDING}/#faq`);
    expect(help('fr')).toBe(`${LANDING}/fr/#faq`);
    vi.stubEnv('NEXT_PUBLIC_LANDING_URL', '');
    expect(help('it')).toBeUndefined();
  });

  it('keeps every app destination reachable', () => {
    const hrefs = new Set(
      (['main', 'workspace', 'account'] as const).flatMap((p) =>
        navEntries(p, true, true).map((e) => e.href),
      ),
    );
    expect([...hrefs].sort()).toEqual([
      '/',
      '/admin',
      '/automations',
      '/billing',
      '/campaigns',
      '/chats',
      '/inbox',
      '/lists',
      '/segments',
      '/settings',
      '/settings/exclusions',
      '/settings/integrations',
      '/settings/profile',
      '/settings/team',
      '/views',
      `${LANDING}/en/#faq`,
    ]);
  });
});

describe('credits', () => {
  const credits = <span> · 942 crediti</span>;

  it('sit in the rail as their own card', () => {
    const html = text(render('/', false, {}, false, credits));
    expect(html).toContain(' 942 crediti ');
    expect(html).not.toContain('ada@example.com · 942');
  });

  it('follow the company in the phone drawer foot', () => {
    expect(
      text(render('/', false, { company: 'Ritmo Sales', isOrgAdmin: false }, true, credits)),
    ).toContain(' Ritmo Sales · 942 crediti ');
  });

  it('show the same role line in the rail and the drawer', () => {
    for (const phone of [false, true]) {
      const admin = text(render('/', false, { company: 'Ritmo Sales' }, phone, credits));
      expect(admin).toContain(phone ? ' Admin · 942 crediti ' : ' Admin ');
      const member = text(
        render('/', false, { company: 'Ritmo Sales', isOrgAdmin: false }, phone, credits),
      );
      expect(member).toContain(' Ritmo Sales');
      expect(member).not.toContain(' Admin ');
    }
  });

  it('does not call a Zenbree staff user an admin of the workspace', () => {
    const html = text(render('/', true, { company: 'Ritmo Sales', isOrgAdmin: false }));
    expect(html).toContain(' Ritmo Sales');
    expect(html).not.toContain(' Admin ');
  });
});

describe('sidebar counts', () => {
  const live = { lists: 1234, campaigns: 4, approvals: 12, views: 3, replies: 2, sessions: [] };

  it('shows none before the first frame', () => {
    nav.live = null;
    expect(text(render('/'))).toContain(
      ' Liste Viste salvate Campagne Automazioni Segmenti Risposte ',
    );
  });

  it('counts lists and campaigns and badges unread replies', () => {
    nav.live = live;
    const html = text(render('/'));
    expect(html).toContain(
      ' Liste 1.234 Viste salvate 3 Campagne 4 Automazioni Segmenti Risposte 2 2 da leggere ',
    );
    expect(html).not.toContain('Da approvare');
    // The count describes the link; its name stays the label.
    expect(render('/')).toMatch(
      /aria-describedby="([^"]+)"[^>]*>.*?<span[^>]*>Liste<\/span><span id="\1" aria-hidden="true"/,
    );
  });

  it('adds Da approvare to the phone menu, with the same counts', () => {
    nav.live = live;
    expect(text(render('/', false, {}, true))).toContain(
      ' Liste 1.234 Viste salvate 3 Campagne 4 Automazioni Segmenti Da approvare 12 Risposte 2 2 da leggere ',
    );
  });

  it('drops the badges at zero but keeps the plain counts', () => {
    nav.live = { ...live, approvals: 0, replies: 0, lists: 0 };
    const html = render('/', false, {}, true);
    expect(text(html)).toContain(
      ' Liste 0 Viste salvate 3 Campagne 4 Automazioni Segmenti Da approvare Risposte ',
    );
    // Only the entries with a count point at one.
    expect(html.match(/aria-describedby=/g)).toHaveLength(3);
  });
});
