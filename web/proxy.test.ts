import { NextRequest } from 'next/server';
import { afterEach, describe, expect, it, vi } from 'vite-plus/test';
import { config, proxy } from './proxy';

const req = (path: string, cookie?: string) =>
  new NextRequest(`https://zenbree.com${path}`, {
    headers: cookie ? { cookie } : {},
  });

describe('proxy', () => {
  afterEach(() => vi.unstubAllEnvs());
  const landing = 'https://www.zenbree.com';

  describe('marketing site', () => {
    const at = (path: string, headers: Record<string, string> = {}) =>
      proxy(new NextRequest(`https://app.zenbree.com${path}`, { headers }));

    it('sends a signed-out / to the landing in the visitor language', () => {
      vi.stubEnv('NEXT_PUBLIC_LANDING_URL', landing);
      const res = at('/');
      expect(res.status).toBe(307);
      expect(res.headers.get('location')).toBe(`${landing}/en/`);
      expect(at('/', { cookie: 'NEXT_LOCALE=it' }).headers.get('location')).toBe(`${landing}/`);
      expect(at('/', { 'accept-language': 'es-ES,es;q=0.9' }).headers.get('location')).toBe(
        `${landing}/es/`,
      );
    });
    it('sends /welcome and its subpaths to the landing, signed in or out', () => {
      vi.stubEnv('NEXT_PUBLIC_LANDING_URL', landing);
      for (const path of ['/welcome', '/welcome/pricing']) {
        for (const cookie of ['', 'better-auth.session_token=x']) {
          const res = at(path, { cookie });
          expect(res.status).toBe(307);
          expect(res.headers.get('location')).toBe(`${landing}/en/`);
        }
      }
    });
    it('keeps a signed-in / in the app', () => {
      vi.stubEnv('NEXT_PUBLIC_LANDING_URL', landing);
      expect(at('/', { cookie: 'better-auth.session_token=x' }).headers.get('location')).toBeNull();
    });
    it('falls back to the app when the landing is empty, malformed or this host', () => {
      for (const value of ['', 'not a url', 'https://app.zenbree.com']) {
        vi.stubEnv('NEXT_PUBLIC_LANDING_URL', value);
        expect(at('/').headers.get('location')).toBe('https://app.zenbree.com/auth/sign-in');
        expect(at('/welcome').headers.get('location')).toBe('https://app.zenbree.com/');
      }
    });
  });

  it('sends other signed-out pages to sign-in', () => {
    expect(proxy(req('/lists')).headers.get('location')).toBe(
      'https://zenbree.com/auth/sign-in?callbackUrl=%2Flists',
    );
  });
  it('passes the current path to the app layout', () => {
    const res = proxy(req('/?s=abc', 'better-auth.session_token=x'));
    expect(res.headers.get('x-middleware-request-x-zb-path')).toBe('/?s=abc');
  });
  it('keeps public pages reachable while routing them through the crawler check', () => {
    const matcher = new RegExp(`^${config.matcher[0]}$`);
    expect(matcher.test('/u/AbC-d_9')).toBe(true);
    expect(matcher.test('/privacy')).toBe(true);
    expect(matcher.test('/usage')).toBe(true);
    expect(matcher.test('/lists')).toBe(true);
    vi.stubEnv('NEXT_PUBLIC_LANDING_URL', landing);
    for (const path of ['/privacy', '/terms', '/u/tok']) {
      expect(proxy(req(path)).headers.get('location')).toBeNull();
    }
  });
  it('leaves sign-in and Better Auth ungated', () => {
    vi.stubEnv('NEXT_PUBLIC_LANDING_URL', landing);
    for (const path of ['/auth/sign-in', '/api/auth/jwks', '/monitoring']) {
      const res = proxy(req(path));
      expect(res.headers.get('location')).toBeNull();
      expect(res.headers.get('x-middleware-next')).toBe('1');
    }
  });
  it('lets a session through', () => {
    vi.stubEnv('NEXT_PUBLIC_LANDING_URL', landing);
    const res = proxy(req('/', 'better-auth.session_token=x'));
    expect(res.headers.get('x-middleware-rewrite')).toBeNull();
    expect(res.headers.get('location')).toBeNull();
  });
  it('blocks declared AI crawler user agents before the login redirect', () => {
    const response = proxy(
      new NextRequest('https://zenbree.com/welcome', { headers: { 'user-agent': 'GPTBot/1.0' } }),
    );
    expect(response.status).toBe(403);
  });

  describe('unsubscribe host (#220)', () => {
    const at = (url: string, init?: { method?: string; headers?: Record<string, string> }) =>
      proxy(new NextRequest(url, init));

    it('serves only /x/<token> on the unsubscribe host', () => {
      vi.stubEnv('UNSUBSCRIBE_ORIGIN', 'https://u.zenbree.com');
      vi.stubEnv('NEXT_PUBLIC_LANDING_URL', landing);
      expect(at('https://u.zenbree.com/x/AbC-d_9').headers.get('x-middleware-rewrite')).toBe(
        'https://u.zenbree.com/u/AbC-d_9',
      );
      for (const path of [
        '/',
        '/welcome',
        '/auth/sign-in',
        '/api/auth/jwks',
        '/u/AbC-d_9',
        '/x/a/b',
      ]) {
        expect(at(`https://u.zenbree.com${path}`).status).toBe(404);
      }
      // Behind the tunnel the server URL is internal; the Host header names the public host.
      const proxied = at('http://web:3000/welcome', { headers: { host: 'u.zenbree.com' } });
      expect(proxied.status).toBe(404);
    });
    it('leaves the one-click POST to the route handler, page actions to the page', () => {
      vi.stubEnv('UNSUBSCRIBE_ORIGIN', 'https://u.zenbree.com');
      const oneClick = at('https://u.zenbree.com/x/tok', { method: 'POST' });
      expect(oneClick.headers.get('x-middleware-rewrite')).toBeNull();
      expect(oneClick.headers.get('x-middleware-next')).toBe('1');
      const action = at('https://u.zenbree.com/x/tok', {
        method: 'POST',
        headers: { 'next-action': 'abc' },
      });
      expect(action.headers.get('x-middleware-rewrite')).toBe('https://u.zenbree.com/u/tok');
    });
    it('redirects links in emails sent before the new host', () => {
      vi.stubEnv('UNSUBSCRIBE_ORIGIN', 'https://u.zenbree.com');
      const res = at('https://zenbree.com/u/tok');
      expect(res.status).toBe(308);
      expect(res.headers.get('location')).toBe('https://u.zenbree.com/x/tok');
    });
    it('keeps /u/<token> on the app host when no unsubscribe host is set', () => {
      vi.stubEnv('UNSUBSCRIBE_ORIGIN', '');
      const res = at('https://zenbree.com/u/tok');
      expect(res.headers.get('location')).toBeNull();
      expect(at('https://zenbree.com/x/tok').headers.get('x-middleware-rewrite')).toBe(
        'https://zenbree.com/u/tok',
      );
    });
  });
});
