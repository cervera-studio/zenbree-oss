import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vite-plus/test';
import { TestIntl } from '@/app/auth/testing';
import { SettingsTabs } from './settings-tabs';

const nav = vi.hoisted(() => ({ pathname: '/settings' }));

vi.mock('next/navigation', () => ({ usePathname: () => nav.pathname }));

function tabs(pathname: string, orgAdmin = true) {
  nav.pathname = pathname;
  return renderToStaticMarkup(
    <TestIntl>
      <SettingsTabs orgAdmin={orgAdmin} />
    </TestIntl>,
  );
}

function currentTabs(pathname: string) {
  return [...tabs(pathname).matchAll(/<a [^>]*aria-current="page"[^>]*>(.*?)<\/a>/g)].map(
    (m) => m[1],
  );
}

describe('SettingsTabs', () => {
  it('marks Profilo on /settings only, not on its sub-pages', () => {
    expect(currentTabs('/settings')).toEqual(['Profilo']);
    expect(currentTabs('/settings/senders')).toEqual(['Mittenti']);
  });

  it('keeps a tab current on its nested routes', () => {
    expect(currentTabs('/settings/integrations/callback')).toEqual(['Integrazioni']);
  });

  it("keeps Profilo current on /settings/profile, the phone menu's Profile page", () => {
    expect(currentTabs('/settings/profile')).toEqual(['Profilo']);
  });

  it('marks Crediti on /billing, which lives outside /settings', () => {
    expect(currentTabs('/billing')).toEqual(['Crediti']);
  });

  it('shows Team to everyone and API key and Crediti only to an admin of the organization', () => {
    const labels = (html: string) => [...html.matchAll(/<a [^>]*>(.*?)<\/a>/g)].map((m) => m[1]);
    expect(labels(tabs('/settings'))).toEqual(
      expect.arrayContaining(['Team', 'API key', 'Crediti']),
    );
    const member = labels(tabs('/settings', false));
    expect(member).toContain('Team');
    expect(member).not.toContain('API key');
    expect(member).not.toContain('Crediti');
  });
});
