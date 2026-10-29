import { getSessionCookie } from 'better-auth/cookies';
import { type NextRequest, NextResponse } from 'next/server';
import { signInPath } from './app/auth/sign-in/params';
import { requestLocale } from './i18n/config';
import { landingUrl } from './lib/landing';

const AI_CRAWLER =
  /Amazonbot|Applebot-Extended|Bytespider|CCBot|ChatGPT-User|ClaudeBot|Claude-Web|cohere-ai|Google-Extended|GPTBot|Meta-ExternalAgent|PerplexityBot/i;

// The unsubscribe link in campaign emails (#220): `<UNSUBSCRIBE_ORIGIN>/x/<token>`, a host that serves
// nothing else. GET (and the page's own server actions) render `/u/<token>`; a plain POST is the RFC 8058
// one-click, handled by `app/(unsubscribe)/x/[token]/route.ts`.
const UNSUBSCRIBE_LINK = /^\/(x|u)\/([\w-]{1,600})$/;
// Sign-in, Better Auth and the error tunnel: no session gate, and only /monitoring on the unsubscribe host.
const UNGATED = /^\/(auth|api\/auth|monitoring)(\/|$)/;

function unsubscribe(request: NextRequest): NextResponse | null {
  const origin = process.env.UNSUBSCRIBE_ORIGIN;
  const host = origin ? new URL(origin).host : null;
  const [, kind, token] = UNSUBSCRIBE_LINK.exec(request.nextUrl.pathname) ?? [];
  // The Host header, not nextUrl: behind the tunnel the server's own URL is the container's.
  if (host && (request.headers.get('host') ?? request.nextUrl.host) === host) {
    if (kind !== 'x' && !request.nextUrl.pathname.startsWith('/monitoring')) {
      return new NextResponse(null, { status: 404 });
    }
  } else if (kind === 'u' && origin) {
    // Emails sent before #220 link the app host: send them to the same page on the new host.
    return NextResponse.redirect(new URL(`/x/${token}`, origin), 308);
  }
  if (kind !== 'x') return null;
  const oneClick = request.method === 'POST' && !request.headers.has('next-action');
  return oneClick ? NextResponse.next() : NextResponse.rewrite(new URL(`/u/${token}`, request.url));
}

// The marketing site in the visitor's language. Null when it is not set, or names this host:
// redirecting there would loop.
function landing(request: NextRequest): URL | null {
  const url = landingUrl(requestLocale(request.headers));
  const host = request.headers.get('host') ?? request.nextUrl.host;
  return url && url.host !== host ? url : null;
}

// Optimistic cookie check only; pages still validate the session server-side.
export function proxy(request: NextRequest) {
  const unsub = unsubscribe(request);
  if (unsub) return unsub;
  if (UNGATED.test(request.nextUrl.pathname)) return NextResponse.next();
  if (AI_CRAWLER.test(request.headers.get('user-agent') ?? '')) {
    return new NextResponse('Forbidden', { status: 403 });
  }
  // 307, not 308, on both redirects below: the target depends on the session cookie and the
  // locale, and a browser caches a 308, so a cached one would strand a visitor who signs in or
  // switches language. The `#faq` of an old link survives: the browser keeps it across a redirect.
  const { pathname } = request.nextUrl;
  if (pathname === '/welcome' || pathname.startsWith('/welcome/')) {
    // The app's marketing page is gone (2026-10-15); with no landing, `/` decides by session.
    return NextResponse.redirect(landing(request) ?? new URL('/', request.url), 307);
  }
  if (!getSessionCookie(request)) {
    // Signed-out visitors at `/` get the marketing site: Google's OAuth verification needs a
    // homepage that is not a login wall. With no landing set, `/` falls through to sign-in.
    const site = pathname === '/' ? landing(request) : null;
    if (site) return NextResponse.redirect(site, 307);
    if (
      ['/privacy', '/terms'].some(
        (path) =>
          request.nextUrl.pathname === path || request.nextUrl.pathname.startsWith(`${path}/`),
      ) ||
      request.nextUrl.pathname.startsWith('/u/')
    ) {
      return NextResponse.next();
    }
    const here = request.nextUrl.pathname + request.nextUrl.search;
    return NextResponse.redirect(new URL(signInPath(here), request.url));
  }
  // The app layout reads this to send an expired session back to the same page.
  const headers = new Headers(request.headers);
  headers.set('x-zb-path', request.nextUrl.pathname + request.nextUrl.search);
  return NextResponse.next({ request: { headers } });
}

// Everything except Next internals and static files: /auth, /api/auth and /monitoring pass through
// `UNGATED`, but the unsubscribe host must still 404 them.
export const config = {
  matcher: ['/((?!_next|.*\\..*).*)'],
};
