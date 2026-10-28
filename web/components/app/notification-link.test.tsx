import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vite-plus/test';
import { NotificationLink } from './notification-link';

vi.mock('next/link', () => ({
  default: ({ href }: { href: string }) => <span data-router={href} />,
}));

const render = (href: string) =>
  renderToStaticMarkup(<NotificationLink href={href}>x</NotificationLink>);

describe('NotificationLink', () => {
  it.each(['/switch?org=o&next=%2Finbox%23reply', '/billing/switch?org=o'])(
    'is a plain anchor, not the router, for %s',
    (href) => {
      expect(render(href)).toMatch(/^<a href="[^"]+">x<\/a>$/);
    },
  );

  it.each(['/switchboard'])('uses the router for %s', (href) => {
    expect(render(href)).toContain('data-router');
  });
});
