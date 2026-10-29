import 'server-only';

import { z } from 'zod';
import type { Camel } from '@/lib/api/camel';
import type { Schema } from '@/lib/api/schema';
import { getToken } from '@/lib/auth-token';
import { getCampaigns } from '@/lib/campaigns/api';
import { httpClient } from '@/lib/http-client';
import { getInstantlyCampaigns } from '@/lib/integrations/instantly';
import { getProjects } from '@/lib/projects/server';
import { type Result, succeed } from '@/lib/types';
import { automationSchema, instantlyCampaignFields } from './api';
import { ORDER } from './definition';
import type { Plan, TriggerBody } from './draft';

const previewSchema = z.looseObject({
  problems: z.array(z.looseObject({ code: z.string(), message: z.string() })),
  steps: z.array(z.looseObject({ type: z.enum(ORDER), creditsEach: z.number() })),
  people: z.number().nullable(),
  withEmail: z.number().nullable(),
  withContact: z.number().nullable(),
  accounts: z.number().nullable(),
  campaign: z
    .looseObject({
      id: z.string(),
      name: z.string(),
      status: z.enum(['draft', 'active', 'paused']),
    })
    .nullable(),
  instantly: z
    .looseObject({
      connected: z.boolean(),
      campaign: z.looseObject({ ...instantlyCampaignFields, url: z.string() }).nullable(),
      unreadable: z.string().nullable(),
    })
    .nullable(),
  signals: z
    .looseObject({
      accounts: z.number(),
      recent: z.array(
        z.looseObject({
          accountId: z.string(),
          name: z.string().nullable(),
          title: z.string().nullable(),
          score: z.number(),
          seenAt: z.string(),
        }),
      ),
    })
    .nullable(),
  peoplePerRun: z.number(),
  accountsPerRun: z.number(),
  maxCredits: z.number(),
}) satisfies z.ZodType<Camel<Schema<'AutomationPreview'>>>;
export type Preview = z.infer<typeof previewSchema>;

const listsSchema = z.looseObject({
  lists: z.array(
    z.looseObject({ id: z.string(), name: z.string(), entity: z.enum(['company', 'person']) }),
  ),
});

const watchlistsSchema = z.looseObject({
  watchlists: z.array(
    z.looseObject({
      id: z.string(),
      name: z.string(),
      enabled: z.boolean(),
      listId: z.string(),
      listName: z.string(),
    }),
  ),
});

export type Choice = { id: string; name: string };
/** A paused watchlist is listed too: the preview names the problem, as for a stopped campaign. `listName`: the
 *  company list the watchlist watches, which a Find people step searches. */
type WatchlistChoice = Choice & { enabled: boolean; listName: string };
/** A failed read never fails the page: only the signal trigger needs the watchlists. */
export type WatchlistChoices = { state: 'ok'; items: WatchlistChoice[] } | { state: 'unreadable' };
type CampaignChoice = Choice & { status: 'draft' | 'active' | 'paused' };
type InstantlyChoice = Choice & { status: number; maySend: boolean };
/** The campaigns the Instantly step can pick. A failed read never fails the page: the step says why it has none. */
type InstantlyChoices =
  | { state: 'ready'; campaigns: InstantlyChoice[] }
  | { state: 'notConnected' }
  | { state: 'unreadable'; code: string };
export type Choices = {
  people: Choice[];
  companies: Choice[];
  campaigns: CampaignChoice[];
  instantly: InstantlyChoices;
  watchlists: WatchlistChoices;
};

export type AutomationBody = Plan & { name: string; trigger: TriggerBody };

const BASE = '/v1/automations';
const at = (id: string) => `${BASE}/${encodeURIComponent(id)}`;

/** A dry run: reads only, so it bills nothing and writes nowhere. `trigger`: the one the draft would save, since
 *  a webhook run works on one person and a signal run on one account. */
export async function previewAutomation(plan: Plan, trigger: TriggerBody, automationId?: string) {
  return httpClient.post(
    `${BASE}/preview`,
    { ...plan, trigger, automationId: automationId ?? null },
    { token: await getToken(), schema: previewSchema },
  );
}

export async function createAutomation(body: AutomationBody) {
  return httpClient.post(
    BASE,
    { ...body, enabled: false },
    { token: await getToken(), schema: automationSchema },
  );
}

export async function updateAutomation(id: string, body: AutomationBody) {
  return httpClient.patch(at(id), body, { token: await getToken(), schema: automationSchema });
}

export async function deleteAutomation(id: string) {
  return httpClient.delete(at(id), { token: await getToken() });
}

export async function getChoices(): Promise<Result<Choices>> {
  const projects = await getProjects();
  if (!projects.success) return projects;
  const project = projects.data.active?.id ?? '';
  const [lists, campaigns, instantly, watchlists] = await Promise.all([
    // Without `project_id` the API answers the lists of every client of the organization.
    httpClient.get(`/v1/lists?project_id=${encodeURIComponent(project)}`, {
      token: await getToken(),
      schema: listsSchema,
    }),
    getCampaigns(),
    getInstantlyChoices(),
    // The watchlists route reads the project from its header, so it names the one the lists come from.
    httpClient.get('/v1/watchlists', {
      token: await getToken(),
      schema: watchlistsSchema,
      project,
    }),
  ]);
  if (!lists.success) return lists;
  if (!campaigns.success) return campaigns;
  const of = (entity: 'company' | 'person') =>
    lists.data.lists.filter((l) => l.entity === entity).map(({ id, name }) => ({ id, name }));
  return succeed({
    people: of('person'),
    companies: of('company'),
    campaigns: campaigns.data.campaigns.flatMap((c) =>
      c.status === 'stopped' || c.status === 'completed'
        ? []
        : [{ id: c.id, name: c.name, status: c.status }],
    ),
    instantly,
    watchlists: watchlists.success
      ? {
          state: 'ok',
          items: watchlists.data.watchlists.map(({ id, name, enabled, listName }) => ({
            id,
            name,
            enabled,
            listName,
          })),
        }
      : { state: 'unreadable' },
  });
}

async function getInstantlyChoices(): Promise<InstantlyChoices> {
  const read = await getInstantlyCampaigns();
  if (read.success) {
    const campaigns = read.data.campaigns.map(({ id, name, status, maySend }) => ({
      id,
      name,
      status,
      maySend,
    }));
    return { state: 'ready', campaigns };
  }
  return read.error.code === 'INSTANTLY.NOT_CONNECTED'
    ? { state: 'notConnected' }
    : { state: 'unreadable', code: read.error.code };
}
