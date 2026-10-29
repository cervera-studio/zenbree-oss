import 'server-only';

import { z } from 'zod';
import { LOCALES } from '@/i18n/config';
import type { Camel } from '@/lib/api/camel';
import type { Schema } from '@/lib/api/schema';
import { getToken } from '@/lib/auth-token';
import { type HomeOverview, overviewSchema } from '@/lib/home/overview';
import { httpClient } from '@/lib/http-client';

const todoSchema = z.looseObject({
  items: z.array(
    z.looseObject({
      kind: z.enum(['approvals', 'replies', 'running']),
      count: z.number(),
      title: z.string().nullable(),
      href: z.string(),
    }),
  ),
}) satisfies z.ZodType<Camel<Schema<'Todo'>>>;

export type TodoItem = Camel<Schema<'TodoItem'>>;

/** `GET /v1/home/todo`: what waits on the user, already ordered and without empty rows. */
export async function getHomeTodo() {
  return httpClient.get('/v1/home/todo', { token: await getToken(), schema: todoSchema });
}

const welcomeSchema = z
  .looseObject({
    companyName: z.string().nullable(),
    knowsFrom: z.enum(['website', 'description', 'none']),
    segments: z.array(
      z.looseObject({
        key: z.string().min(1),
        name: z.string(),
        titles: z.array(z.string()),
        employeeRange: z.string().nullable(),
        message: z.string().min(1),
        locale: z.enum(LOCALES).nullable(),
      }),
    ),
    steps: z
      .array(z.looseObject({ kind: z.enum(['profile', 'list', 'mailbox']), done: z.boolean() }))
      .length(3),
  })
  .nullable() satisfies z.ZodType<Camel<Schema<'Welcome'>> | null>;

export type Welcome = Camel<Schema<'Welcome'>>;
export type StepKind = Welcome['steps'][number]['kind'];

/** `GET /v1/home/welcome`: null when the first-access Home must not show. A failure hides it too. */
export async function getHomeWelcome(locale: string): Promise<Welcome | null> {
  const res = await httpClient.get('/v1/home/welcome', {
    token: await getToken(),
    params: { locale },
    schema: welcomeSchema,
    timeoutMs: 2500,
  });
  return res.success ? res.data : null;
}

/** `POST /v1/home/welcome/dismiss`: latches the first-access Home closed for good. */
export async function dismissHomeWelcome() {
  return httpClient.post('/v1/home/welcome/dismiss', undefined, { token: await getToken() });
}

export async function getHomeOverview(): Promise<HomeOverview | null> {
  const res = await httpClient.get('/v1/home/overview', {
    token: await getToken(),
    schema: overviewSchema,
    timeoutMs: 2500,
  });
  return res.success ? res.data : null;
}

const kpiSchema = z.looseObject({ value: z.number(), previous: z.number() });

const statsSchema = z.looseObject({
  kpis: z.looseObject({
    leads: kpiSchema,
    contacts: kpiSchema,
    messagesSent: kpiSchema,
    replies: kpiSchema,
    positiveReplies: kpiSchema,
  }),
  weeks: z.array(
    z.looseObject({ week: z.string(), messagesSent: z.number(), replies: z.number() }),
  ),
  funnel: z.looseObject({
    leads: z.number(),
    contacts: z.number(),
    contacted: z.number(),
    replied: z.number(),
    positive: z.number(),
  }),
  activity: z.array(
    z.looseObject({
      kind: z.enum(['search_finished', 'campaign_started', 'reply_received']),
      at: z.string(),
      title: z.string().nullable(),
      link: z.string().nullable(),
    }),
  ),
}) satisfies z.ZodType<Camel<Schema<'Stats'>>>;

export type HomeStats = Camel<Schema<'Stats'>>;

/** `GET /v1/home/stats`: the dashboard numbers of the active project. A failure hides the panels. */
export async function getHomeStats(): Promise<HomeStats | null> {
  const res = await httpClient.get('/v1/home/stats', {
    token: await getToken(),
    schema: statsSchema,
    timeoutMs: 2500,
  });
  return res.success ? res.data : null;
}
