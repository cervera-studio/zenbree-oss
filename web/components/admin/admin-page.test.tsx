// @vitest-environment happy-dom
import { describe, expect, it } from 'vite-plus/test';
import { render } from '@/app/auth/testing';
import { AdminPage, sectionCrumbs } from './admin-page';

describe('sectionCrumbs', () => {
  it('is Backoffice, then the section as the page itself', () => {
    expect(sectionCrumbs('Backoffice', 'Job')).toEqual([
      { label: 'Backoffice', href: '/admin' },
      { label: 'Job' },
    ]);
  });
});

describe('AdminPage', () => {
  it('links every crumb but the last, which is the current page', async () => {
    const c = await render(
      <AdminPage
        crumbs={[{ label: 'Utenti', href: '/admin' }, { label: 'ada@example.com' }]}
        title="Ada"
        meta="2 messaggi"
      >
        body
      </AdminPage>,
    );
    const links = [...c.querySelectorAll('header nav a')].map((a) => [
      a.textContent,
      a.getAttribute('href'),
    ]);
    expect(links).toEqual([['Utenti', '/admin']]);
    expect(c.querySelector('header [aria-current="page"]')?.textContent).toBe('ada@example.com');
    expect(c.querySelector('h1')?.textContent).toBe('Ada');
    expect(c.querySelector('main')?.textContent).toContain('2 messaggi');
  });
});
