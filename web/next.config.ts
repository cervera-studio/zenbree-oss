import type { NextConfig } from 'next';
import createNextIntlPlugin from 'next-intl/plugin';

const withNextIntl = createNextIntlPlugin('./i18n/request.ts');

// Profile photos are served by the API (#503), a different origin from the app.
const apiOrigin = new URL(process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000').origin;

function buildWorkers(value: string | undefined): { cpus?: number } {
  const cpus = Number(value);
  return Number.isInteger(cpus) && cpus > 0 ? { cpus } : {};
}

const nextConfig: NextConfig = {
  output: 'standalone',
  // This project identification URL is public and must reach the browser bundle.
  env: {
    BETTER_AUTH_IDENTIFY_URL: process.env.BETTER_AUTH_IDENTIFY_URL || '',
  },
  // The verify stack builds under a 2.75 GiB ceiling on a shared Docker VM (.claude/skills/verify/compose.verify.yml).
  // Its gate already ran `pnpm typecheck`, so it skips the check. Every other build still checks types.
  typescript: { ignoreBuildErrors: process.env.NEXT_BUILD_SKIP_TYPECHECK === '1' },
  // pg is a Node-only driver; keep it out of the server bundle.
  serverExternalPackages: ['pg'],
  // forbidden() in the /admin layout.
  // Next starts one page-data worker per host CPU but one: 11 here took the build to 3.2 GiB, 2 to 2.4 GiB.
  experimental: { authInterrupts: true, ...buildWorkers(process.env.NEXT_BUILD_WORKERS) },
  images: { remotePatterns: [{ hostname: 'lh3.googleusercontent.com' }] },
  // Only img-src: a script-src would block Next's inline scripts. The s2 favicon service redirects to
  // tN.gstatic.com, and CSP checks the redirect target too.
  headers: async () => [
    {
      source: '/(.*)',
      headers: [
        {
          key: 'Content-Security-Policy',
          value: `img-src 'self' data: ${apiOrigin} https://lh3.googleusercontent.com https://www.google.com/s2/favicons https://*.gstatic.com`,
        },
      ],
    },
  ],
};

// Errors go to Better Stack through its compatible Next.js client; no source maps are uploaded.
export default withNextIntl(nextConfig);
