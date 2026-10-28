import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vite-plus/test';
import { TestIntl } from '@/app/auth/testing';
import { SettingsShell } from './settings-shell';

const nav = vi.hoisted(() => ({ pathname: '/settings' }));

vi.mock('next/navigation', () => ({ usePathname: () => nav.pathname }));

function shell(pathname: string) {
  nav.pathname = pathname;
  const html = renderToStaticMarkup(
    <TestIntl>
      <SettingsShell orgAdmin>
        <p>pagina</p>
      </SettingsShell>
    </TestIntl>,
  );
  const links = (scope: string) =>
    [...scope.matchAll(/<a [^>]*href="([^"]*)"[^>]*>(.*?)<\/a>/g)].map((m) => ({
      href: m[1],
      label: (m[2] ?? '').replace(/<[^>]*>/g, ''),
    }));
  const menu = html.match(/<nav [^>]*desktop:hidden[^>]*>(.*?)<\/nav>/)?.[1];
  return {
    menu: menu ? links(menu) : null,
    back: links(html).find(({ label }) => label.includes('Tutte le impostazioni')) ?? null,
    pageHiddenOnPhone: /<div class="contents phone:hidden"><p>pagina/.test(html),
  };
}

describe('SettingsShell on a phone', () => {
  it('turns /settings into the menu, with Profile on a page of its own', () => {
    const { menu, back, pageHiddenOnPhone } = shell('/settings');
    expect(menu?.map(({ href }) => href)).toEqual([
      '/settings/profile',
      '/settings/notifications',
      '/settings/senders',
      '/settings/integrations',
      '/settings/memory',
      '/settings/exclusions',
      '/settings/client',
      '/settings/team',
      '/settings/api-keys',
      '/settings/webhooks',
      '/billing',
    ]);
    expect(menu?.[0]?.label).toBe('Profilo');
    expect(back).toBeNull();
    expect(pageHiddenOnPhone).toBe(true);
  });

  it.each([
    '/settings/profile',
    '/settings/senders',
    '/settings/integrations/callback',
    '/billing',
  ])('opens %s full screen under a back link to the menu', (pathname) => {
    const { menu, back, pageHiddenOnPhone } = shell(pathname);
    expect(menu).toBeNull();
    expect(back?.href).toBe('/settings');
    expect(pageHiddenOnPhone).toBe(false);
  });
});
