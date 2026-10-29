import { z } from 'zod';
import type { Camel } from '@/lib/api/camel';
import type { Schema } from '@/lib/api/schema';
import type { Step } from './steps';

export const CONTACT_TOKENS = ['firstName', 'lastName', 'companyName', 'role'] as const;
export const SIGNAL_KEYS = ['title', 'city', 'country', 'url'] as const;
export const MAX_FIELDS = 20;
export const MAX_FALLBACK = 200;

const sourceSchema = z.discriminatedUnion('kind', [
  z.looseObject({ kind: z.literal('column'), key: z.string() }),
  z.looseObject({ kind: z.literal('signal'), key: z.enum(SIGNAL_KEYS) }),
]);

export const campaignFieldSchema = z.looseObject({
  token: z.string(),
  source: sourceSchema,
  fallback: z.string().nullable().optional(),
}) satisfies z.ZodType<Camel<Schema<'CampaignField'>>>;
export type CampaignField = z.infer<typeof campaignFieldSchema>;

const entrySchema = z.looseObject({
  token: z.string(),
  source: sourceSchema,
  label: z.string().nullable(),
  ai: z.boolean(),
  filled: z.number(),
});
export type CatalogEntry = z.infer<typeof entrySchema>;

export const fieldCatalogSchema = z.looseObject({
  people: z.number(),
  contactTokens: z.array(z.string()),
  columns: z.array(entrySchema),
  signal: z.array(entrySchema),
}) satisfies z.ZodType<Camel<Schema<'FieldCatalog'>>>;
export type FieldCatalog = z.infer<typeof fieldCatalogSchema>;

const sameSource = (a: CampaignField['source'], b: CampaignField['source']) =>
  a.kind === b.kind && a.key === b.key;

export function withField(
  fields: CampaignField[],
  entry: CatalogEntry,
): { fields: CampaignField[]; token: string } {
  const existing = fields.find((f) => sameSource(f.source, entry.source));
  if (existing) return { fields, token: existing.token };
  const taken = new Set(fields.map((f) => f.token));
  let token = entry.token;
  for (let n = 2; taken.has(token); n++) token = `${entry.token}${n}`;
  return { fields: [...fields, { token, source: entry.source, fallback: null }], token };
}

export function entryOf(field: Pick<CampaignField, 'source'>, catalog: FieldCatalog | null) {
  const entries = field.source.kind === 'column' ? catalog?.columns : catalog?.signal;
  return entries?.find((e) => sameSource(e.source, field.source));
}

export const usesToken = (steps: Step[], token: string) =>
  steps.some((s) => `${s.subject}\n${s.body}`.includes(`{{${token}}}`));

export const blankFallbacks = (fields: CampaignField[]) =>
  fields.filter((f) => typeof f.fallback === 'string' && !f.fallback.trim()).map((f) => f.token);

export function toApiFields(fields: CampaignField[]): CampaignField[] {
  return fields.map((f) => ({
    token: f.token,
    source:
      f.source.kind === 'signal'
        ? { kind: 'signal', key: f.source.key }
        : { kind: 'column', key: f.source.key },
    fallback: f.fallback?.trim() || null,
  }));
}
