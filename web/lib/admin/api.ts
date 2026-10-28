/**
 * The /v1/admin calls and their response contracts (the chat ones are in
 * chats.ts, as api/app/admin/chats.py). Shapes from feature/m1-billing-admin
 * (api/app/admin/routes.py, costs.py) and m1-agent's schema_agent.sql.
 *
 * Money: costs are USD floats (`*CostUsd`), revenue is EUR (`revenueEurCents`
 * int, `revenueEur` float), `marginEur` is computed server-side at `eurPerUsd`.
 * Credits are integers. Keys arrive camelCased by httpClient.
 */
import 'server-only';

import { z } from 'zod';
import type { Camel } from '@/lib/api/camel';
import type { Schema } from '@/lib/api/schema';
import { getToken } from '@/lib/auth-token';
import { ledgerEntryShape, subscriptionStateSchema } from '@/lib/billing/schemas';
import { httpClient } from '@/lib/http-client';

const money = {
  llmCostUsd: z.number(),
  vendorCostUsd: z.number(),
  costUsd: z.number(),
  creditsSpent: z.number(),
  revenueEurCents: z.number(),
  revenueEur: z.number(),
  marginEur: z.number(),
};

export const costsSchema = z.looseObject({
  from: z.string(),
  to: z.string(),
  eurPerUsd: z.number(),
  totals: z.looseObject({ ...money, creditsPurchased: z.number() }),
  users: z.array(
    z.looseObject({
      ...money,
      userId: z.string().nullable(),
      email: z.string().nullable(),
      creditsPurchased: z.number(),
    }),
  ),
  daily: z.array(z.looseObject({ ...money, day: z.string() })),
  byVendor: z.array(
    z.looseObject({
      vendor: z.string(),
      calls: z.number(),
      units: z.number(),
      costUsd: z.number(),
    }),
  ),
  // Email/phone waterfall: lookups per provider, and how many found the channel.
  byProvider: z.array(
    z.looseObject({
      provider: z.string(),
      channel: z.enum(['email', 'phone']),
      lookups: z.number(),
      hits: z.number(),
    }),
  ),
}) satisfies z.ZodType<Camel<Schema<'Costs'>>>;
export type Costs = z.infer<typeof costsSchema>;

const ledgerEntry = z.looseObject({
  ...ledgerEntryShape,
  referenceId: z.string().nullable(),
}) satisfies z.ZodType<Camel<Schema<'LedgerEntry'>>>;

const purchase = z.looseObject({
  id: z.string(),
  packId: z.string(),
  credits: z.number(),
  amountCents: z.number(),
  currency: z.string(),
  refundedCents: z.number(),
  stripeSessionId: z.string().nullable(),
  createdAt: z.string(),
}) satisfies z.ZodType<Camel<Schema<'Purchase'>>>;

const usageSchema = z.looseObject({
  userId: z.string(),
  email: z.string(),
  balance: z.number().nullable(),
  company: z.string().nullable(),
  // Their own organization's live plan, null without one.
  subscription: subscriptionStateSchema.nullable(),
  ledger: z.array(ledgerEntry),
  ledgerTotal: z.number(),
  purchases: z.array(purchase),
  organizations: z.array(z.looseObject({ id: z.string(), name: z.string(), role: z.string() })),
  eurPerUsd: z.number(),
  costs: costsSchema.pick({ totals: true, daily: true, byVendor: true }),
}) satisfies z.ZodType<Camel<Schema<'UserUsage'>>>;

export const page = <T extends z.ZodType>(item: T) =>
  z.looseObject({ items: z.array(item), nextOffset: z.number().nullable() });

// ponytail: the API returns `jobs.*`; only what the table renders is typed.
const jobsPageSchema = page(
  z.looseObject({
    chatSessionId: z.string().nullable(),
    brief: z.string().nullable(),
    stages: z
      .array(
        z.looseObject({
          tool: z.string(),
          label: z.string().nullish(),
          units: z.number(),
          done: z.number(),
          error: z.unknown().optional(),
          // From its first call to its last result; absent on jobs from before #352.
          startedAt: z.string().optional(),
          finishedAt: z.string().optional(),
        }),
      )
      .nullable(),
    id: z.string(),
    userId: z.string().nullable(),
    email: z.string().nullable(),
    status: z.enum(['queued', 'running', 'needs_input', 'succeeded', 'failed', 'cancelled']),
    phase: z.string().nullable(),
    progressDone: z.number(),
    progressTotal: z.number(),
    creditsReserved: z.number(),
    creditsSpent: z.number(),
    error: z.string().nullable(),
    createdAt: z.string(),
    startedAt: z.string().nullable(),
    firstRowAt: z.string().nullable(),
    finishedAt: z.string().nullable(),
    // Rows given back at the end and the credits they returned; absent on jobs from before #352.
    result: z
      .looseObject({ refunded: z.number().optional(), undelivered: z.number().optional() })
      .nullable()
      .catch(null),
  }),
).extend({
  // Keyed by status, camelCased on the way in (needs_input → needsInput).
  counts: z.record(z.string(), z.number()),
});
type JobRow = Pick<
  Camel<Schema<'Job'>>,
  | 'chatSessionId'
  | 'brief'
  | 'stages'
  | 'id'
  | 'userId'
  | 'email'
  | 'status'
  | 'phase'
  | 'progressDone'
  | 'progressTotal'
  | 'creditsReserved'
  | 'creditsSpent'
  | 'error'
  | 'createdAt'
  | 'startedAt'
  | 'firstRowAt'
  | 'finishedAt'
  | 'result'
>;
const jobsSchema = jobsPageSchema satisfies z.ZodType<
  Pick<Camel<Schema<'JobPage'>>, 'nextOffset' | 'counts'> & { items: JobRow[] }
>;
export type Job = z.infer<typeof jobsSchema>['items'][number];

const settingsSchema = z.looseObject({
  settings: z.array(
    z.looseObject({
      key: z.string(),
      value: z.unknown(),
      updatedAt: z.string(),
      updatedBy: z.string().nullable(),
      updatedByName: z.string().nullable(),
    }),
  ),
  // Vendors with failed calls in the last 24 hours, most first.
  vendorErrors: z.array(
    z.looseObject({ vendor: z.string(), errors: z.number(), lastAt: z.string() }),
  ),
}) satisfies z.ZodType<Camel<Schema<'Settings'>>>;

const userBalanceSchema = z.looseObject({
  userId: z.string(),
  balance: z.number(),
}) satisfies z.ZodType<Camel<Schema<'UserBalance'>>>;

const organizationBalanceSchema = z.looseObject({
  organizationId: z.string(),
  balance: z.number(),
}) satisfies z.ZodType<Camel<Schema<'OrganizationBalance'>>>;

const organizationRow = z.looseObject({
  id: z.string(),
  name: z.string(),
  slug: z.string(),
  createdAt: z.string(),
  balance: z.number().nullable(),
});

const organizationsSchema = z.looseObject({
  items: z.array(
    organizationRow.extend({
      members: z.number(),
      planId: z.string().nullable(),
      hasPurchases: z.boolean(),
    }),
  ),
  total: z.number(),
}) satisfies z.ZodType<Camel<Schema<'OrganizationPage'>>>;

const organizationSchema = organizationRow.extend({
  members: z.array(
    z.looseObject({
      userId: z.string(),
      email: z.string(),
      name: z.string(),
      role: z.string(),
      createdAt: z.string(),
    }),
  ),
  subscription: subscriptionStateSchema.nullable(),
  ledger: z.array(ledgerEntry.extend({ email: z.string().nullable() })),
  ledgerTotal: z.number(),
  purchases: z.array(purchase),
  projects: z.array(
    z.looseObject({
      id: z.string(),
      name: z.string(),
      createdAt: z.string(),
      creditCap: z.number().nullable(),
      creditCapMode: z.enum(['hard', 'soft']).nullable(),
      creditsThisMonth: z.number(),
    }),
  ),
}) satisfies z.ZodType<Camel<Schema<'OrganizationDetail'>>>;
export type Organization = z.infer<typeof organizationSchema>;

export const auth = async () => ({ token: await getToken() });

export async function getCosts(from?: string, to?: string) {
  return httpClient.get('/v1/admin/costs', {
    ...(await auth()),
    params: { from, to },
    schema: costsSchema,
  });
}

export async function getUsage(userId: string) {
  return httpClient.get(`/v1/admin/users/${encodeURIComponent(userId)}/usage`, {
    ...(await auth()),
    schema: usageSchema,
  });
}

export async function adjustCredits(userId: string, amount: number, reason: string) {
  return httpClient.post(
    `/v1/admin/users/${encodeURIComponent(userId)}/credits`,
    { amount, reason },
    { ...(await auth()), schema: userBalanceSchema },
  );
}

export async function getOrganizations(q: string, offset: number, limit: number) {
  return httpClient.get('/v1/admin/organizations', {
    ...(await auth()),
    params: { q: q || undefined, offset, limit },
    schema: organizationsSchema,
  });
}

export async function getOrganization(id: string) {
  return httpClient.get(`/v1/admin/organizations/${encodeURIComponent(id)}`, {
    ...(await auth()),
    schema: organizationSchema,
  });
}

export async function adjustOrganizationCredits(id: string, amount: number, reason: string) {
  return httpClient.post(
    `/v1/admin/organizations/${encodeURIComponent(id)}/credits`,
    { amount, reason },
    { ...(await auth()), schema: organizationBalanceSchema },
  );
}

export async function getJobs(
  offset: number,
  filters: { status?: string; userId?: string; jobId?: string },
) {
  return httpClient.get('/v1/admin/jobs', {
    ...(await auth()),
    params: {
      limit: 50,
      offset,
      status: filters.status,
      user_id: filters.userId,
      job_id: filters.jobId,
    },
    schema: jobsSchema,
  });
}

const overviewSchema = z.looseObject({
  users: z.array(
    z.looseObject({
      userId: z.string(),
      balance: z.number().nullable(),
      company: z.string().nullable(),
      planId: z.string().nullable(),
      hasPurchases: z.boolean(),
      lastActiveAt: z.string().nullable(),
    }),
  ),
}) satisfies z.ZodType<Camel<Schema<'UsersOverview'>>>;
export type UserOverview = z.infer<typeof overviewSchema>['users'][number];
export async function getUsersOverview(ids: string[]) {
  return httpClient.get('/v1/admin/users/overview', {
    ...(await auth()),
    params: { ids },
    schema: overviewSchema,
  });
}

const badgesSchema = z.looseObject({
  jobsAttention: z.number(),
  switchesOn: z.number(),
}) satisfies z.ZodType<Camel<Schema<'Badges'>>>;

export async function getBadges() {
  return httpClient.get('/v1/admin/badges', { ...(await auth()), schema: badgesSchema });
}

export async function getSettings() {
  return httpClient.get('/v1/admin/settings', { ...(await auth()), schema: settingsSchema });
}

/** A new `vendor_disabled:`/`channel_disabled:` key, off; 409 `ADMIN.SETTING_EXISTS` if it exists. */
export async function addSetting(key: string) {
  return httpClient.post(
    '/v1/admin/settings',
    { key },
    {
      ...(await auth()),
      schema: settingsSchema,
    },
  );
}

/** `value: null` deletes the key. */
export async function putSetting(key: string, value: unknown) {
  return httpClient.put(
    '/v1/admin/settings',
    { key, value },
    {
      ...(await auth()),
      schema: settingsSchema,
    },
  );
}
