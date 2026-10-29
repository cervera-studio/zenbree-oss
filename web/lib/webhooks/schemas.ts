import { z } from 'zod';
import type { Camel } from '@/lib/api/camel';
import type { Schema } from '@/lib/api/schema';
import { hostOf } from './host';

const wireWebhookSchema = z.looseObject({
  id: z.string(),
  url: z.string(),
  label: z.string(),
  eventNames: z.array(z.string()),
  createdAt: z.string(),
  goneAt: z.string().nullable(),
}) satisfies z.ZodType<Camel<Schema<'Webhook'>>>;
// The URL stops here: a token in its path must not reach the page payload or the client state.
const toPublic = ({ url, ...rest }: z.infer<typeof wireWebhookSchema>) => ({
  ...rest,
  host: hostOf(url),
});
const webhookSchema = wireWebhookSchema.transform(toPublic);
const wireListSchema = z.looseObject({
  webhooks: z.array(wireWebhookSchema),
  events: z.array(z.string()),
}) satisfies z.ZodType<Camel<Schema<'Webhooks'>>>;
export const listSchema = wireListSchema.transform(({ webhooks, ...rest }) => ({
  ...rest,
  webhooks: webhooks.map(toPublic),
}));
const wireCreatedSchema = wireWebhookSchema.extend({ secret: z.string() }) satisfies z.ZodType<
  Camel<Schema<'WebhookWithSecret'>>
>;
export const createdSchema = wireCreatedSchema.transform(({ secret, ...rest }) => ({
  ...toPublic(rest),
  secret,
}));
export const testSchema = z.looseObject({ status: z.number().nullable() }) satisfies z.ZodType<
  Camel<Schema<'WebhookTestResult'>>
>;
type Category = NonNullable<Schema<'Delivery'>['category']>;
// A record, not a list: a category the API adds or drops fails the typecheck here, where an enum that is
// merely assignable to the generated type would accept a missing one.
const CATEGORIES: Record<Category, true> = {
  timeout: true,
  blocked: true,
  unreachable: true,
  rate_limited: true,
  server_error: true,
  rejected: true,
  gone: true,
};
const FAILURE_CATEGORIES = Object.keys(CATEGORIES) as [Category, ...Category[]];
const deliverySchema = z.looseObject({
  id: z.string(),
  eventId: z.string(),
  eventType: z.string(),
  isTest: z.boolean(),
  attempt: z.number(),
  attemptedAt: z.string(),
  statusCode: z.number().nullable(),
  category: z.enum(FAILURE_CATEGORIES).nullable(),
  willRetry: z.boolean(),
}) satisfies z.ZodType<Camel<Schema<'Delivery'>>>;
export const deliveriesSchema = z.looseObject({
  deliveries: z.array(deliverySchema),
}) satisfies z.ZodType<Camel<Schema<'Deliveries'>>>;

export type Webhook = z.infer<typeof webhookSchema>;
export type WebhookDelivery = Camel<Schema<'Delivery'>>;
