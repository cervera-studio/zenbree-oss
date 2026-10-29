import type { BetterAuthPlugin } from 'better-auth';
import { APIError, createAuthMiddleware } from 'better-auth/api';

// #978: dash() serves Better Auth Infra's dashboard under an Infra-signed JWT, which ranks above Zenbree's admin
// role. Its write routes skip the rules of team.ts (founder, sender release) and the admin plugin's hooks, so only
// these reads pass; any other /dash route, including one a later Infra version adds, answers 404. ctx.path is the
// endpoint's declared path, so no URL spelling reaches a handler, and the hook runs before the route's JWT check.
const READS = new Set([
  '/dash/config',
  '/dash/validate',
  '/dash/list-users',
  '/dash/export-users',
  '/dash/user',
  '/dash/user-organizations',
  '/dash/user-stats',
  '/dash/user-graph-data',
  '/dash/user-retention-data',
  '/dash/list-organizations',
  '/dash/export-organizations',
  '/dash/organization/options',
  '/dash/organization/:id',
  '/dash/organization/:id/members',
  '/dash/organization/:id/invitations',
  '/dash/organization/:id/teams',
]);

export const dashGuard = () =>
  ({
    id: 'dash-guard',
    hooks: {
      before: [
        {
          matcher: (ctx) => (ctx.path ?? '').startsWith('/dash') && !READS.has(ctx.path ?? ''),
          handler: createAuthMiddleware(async () => {
            throw new APIError('NOT_FOUND', { message: 'Not found' });
          }),
        },
      ],
    },
  }) satisfies BetterAuthPlugin;
