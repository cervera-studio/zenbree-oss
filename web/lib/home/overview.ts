import { z } from 'zod';
import type { Camel } from '@/lib/api/camel';
import type { Schema } from '@/lib/api/schema';
import { toCamelCase } from '@/lib/http-client/case-transform';

const personSchema = z.looseObject({ id: z.string(), name: z.string() });

const entitySchema = z.enum(['company', 'person']);

export const overviewSchema = z.looseObject({
  approvals: z.looseObject({
    total: z.number(),
    campaigns: z.array(z.looseObject({ id: z.string(), name: z.string(), count: z.number() })),
  }),
  replies: z.looseObject({
    count: z.number(),
    latest: z
      .looseObject({
        name: z.string(),
        campaign: z.string().nullable(),
        intent: z.enum(['interested', 'meeting_request']).nullable(),
        at: z.string(),
      })
      .nullable(),
  }),
  running: z.array(
    z.looseObject({
      jobId: z.string(),
      listId: z.string().nullable(),
      newList: z.boolean(),
      sessionId: z.string(),
      title: z.string().nullable(),
      stageTool: z.string().nullable(),
      entity: entitySchema.nullable(),
      stagesDone: z.number(),
      stagesTotal: z.number(),
      progressDone: z.number(),
      progressTotal: z.number(),
      rows: z.number(),
      startedBy: personSchema.nullable(),
      startedAt: z.string(),
    }),
  ),
  finished: z.array(
    z.looseObject({
      jobId: z.string(),
      sessionId: z.string(),
      title: z.string().nullable(),
      entity: entitySchema,
      listId: z.string(),
      newList: z.boolean(),
      rows: z.number(),
      verifiedEmails: z.number().nullable(),
      stagesTotal: z.number(),
    }),
  ),
  lists: z.array(
    z.looseObject({
      id: z.string(),
      name: z.string(),
      entity: entitySchema,
      mode: z.enum(['build', 'enrich']),
      rows: z.number(),
      verifiedEmails: z.number().nullable(),
      updatedAt: z.string(),
      owner: personSchema.nullable(),
    }),
  ),
  campaigns: z.array(
    z.looseObject({
      id: z.string(),
      name: z.string(),
      status: z.enum(['active', 'paused']),
      channels: z.array(z.enum(['email', 'linkedin', 'hubspot'])),
      sent: z.number(),
      replies: z.number(),
      owner: personSchema.nullable(),
    }),
  ),
  campaignHint: z
    .looseObject({ listId: z.string(), name: z.string(), verifiedEmails: z.number() })
    .nullable(),
}) satisfies z.ZodType<Camel<Schema<'Overview'>>>;

export type HomeOverview = Camel<Schema<'Overview'>>;
export type RunningSearch = HomeOverview['running'][number];
export type FinishedSearch = HomeOverview['finished'][number];

export function parseOverviewFrame(data: string): HomeOverview | null {
  try {
    const parsed = overviewSchema.safeParse(toCamelCase(JSON.parse(data)));
    return parsed.success ? parsed.data : null;
  } catch {
    return null;
  }
}
