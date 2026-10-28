import { z } from 'zod';
import { api } from '@/lib/api/browser';
import { API_ROUTES } from '@/lib/config/api-routes';
import { OUTCOMES } from '@/lib/integrations/instantly-campaign';

const campaignSchema = z.looseObject({
  id: z.string(),
  name: z.string(),
  status: z.number(),
  status_label: z.string(),
  may_send: z.boolean(),
});
export type InstantlyCampaign = z.infer<typeof campaignSchema>;

const previewSchema = z.looseObject({
  campaign: z.looseObject({
    id: z.string(),
    name: z.string(),
    may_send: z.boolean(),
    url: z.string(),
  }),
  selected: z.number(),
  eligible: z.number(),
  skipped: z.record(z.string(), z.number()),
  rows: z.array(
    z.looseObject({
      row_id: z.string(),
      email: z.string().nullable(),
      reason: z.string().nullable(),
    }),
  ),
  sample: z.array(z.looseObject({ row_id: z.string(), lead: z.record(z.string(), z.unknown()) })),
  custom_variables: z.array(z.string()),
});
export type InstantlyPreview = z.infer<typeof previewSchema>;

const enrollmentSchema = z.looseObject({
  id: z.string(),
  status: z.enum(['queued', 'running', 'completed', 'cancelled', 'failed']),
  error_code: z.string().nullable(),
  campaign: z.looseObject({ id: z.string(), name: z.string(), url: z.string() }),
  counts: z.record(z.enum(OUTCOMES), z.number()),
  cancel_requested: z.boolean(),
  can_cancel: z.boolean(),
  can_retry: z.boolean(),
  rows: z.array(
    z.looseObject({
      row_id: z.string().nullable(),
      email: z.string().nullable(),
      outcome: z.enum(OUTCOMES),
      reason: z.string().nullable(),
    }),
  ),
});
export type InstantlyEnrollment = z.infer<typeof enrollmentSchema>;

/** Instantly field -> list column key ('' leaves the field out); `custom` are columns sent as custom variables. */
export type InstantlyMapping = { fields: Record<string, string>; custom: string[] };
export type InstantlyTarget = {
  listId: string;
  rowIds: string[];
  campaignId: string;
  mapping: InstantlyMapping;
};

const body = (t: InstantlyTarget) => ({
  list_id: t.listId,
  row_ids: t.rowIds,
  campaign_id: t.campaignId,
  mapping: t.mapping,
});
const wire = { camel: false } as const;

export const instantlyApi = {
  campaigns: () =>
    api(API_ROUTES.instantly.campaigns, {
      ...wire,
      schema: z.looseObject({ campaigns: z.array(campaignSchema) }),
    }),
  preview: (t: InstantlyTarget) =>
    api(API_ROUTES.instantly.preview, {
      ...wire,
      method: 'POST',
      body: body(t),
      schema: previewSchema,
    }),
  // `acknowledgeSending` is the user's second consent, asked when the campaign is running in Instantly.
  enroll: (t: InstantlyTarget, acknowledgeSending: boolean) =>
    api(API_ROUTES.instantly.enrollments, {
      ...wire,
      method: 'POST',
      body: { ...body(t), confirm: true, acknowledge_sending: acknowledgeSending },
      schema: enrollmentSchema,
    }),
  latest: () => api(API_ROUTES.instantly.latest, { ...wire, schema: enrollmentSchema.nullable() }),
  get: (id: string) =>
    api(API_ROUTES.instantly.enrollment(id), { ...wire, schema: enrollmentSchema }),
  cancel: (id: string) =>
    api(API_ROUTES.instantly.cancel(id), { ...wire, method: 'POST', schema: enrollmentSchema }),
  retry: (id: string) =>
    api(API_ROUTES.instantly.retry(id), { ...wire, method: 'POST', schema: enrollmentSchema }),
};
