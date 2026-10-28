import { z } from 'zod';
import type { Camel } from '@/lib/api/camel';
import type { Schema } from '@/lib/api/schema';

export type Definition = Camel<Schema<'Definition'>>;
export type DefinitionStep = Definition['steps'][number];
export type StepKind = DefinitionStep['type'];

// `api.Definition`: the order the steps run in, each at most once.
export const ORDER = [
  'find_people',
  'qualify',
  'find_emails',
  'add_to_campaign',
  'add_to_instantly',
  'write_hubspot',
] as const satisfies readonly StepKind[];

const text = z.string().min(1).max(200);

export const definitionSchema = z.object({
  listId: z.uuid(),
  steps: z
    .array(
      z.discriminatedUnion('type', [
        z.object({
          type: z.literal('find_people'),
          accountsListId: z.uuid(),
          titles: z.array(text).min(1).max(10),
          seniorities: z.array(text).max(10),
          perAccount: z.int().min(1).max(5),
        }),
        z.object({
          type: z.literal('qualify'),
          brief: z.string().min(1).max(1000),
          minScore: z.int().min(0).max(100),
        }),
        z.object({ type: z.literal('find_emails') }),
        z.object({ type: z.literal('add_to_campaign'), campaignId: z.uuid() }),
        z.object({ type: z.literal('add_to_instantly'), campaignId: z.uuid() }),
        z.object({ type: z.literal('write_hubspot') }),
      ]),
    )
    .min(1)
    .max(ORDER.length),
}) satisfies z.ZodType<Definition>;

export const MAX_CREDIT_CAP = 100;

// `apollo_models.APOLLO_SENIORITIES`, the values the people search takes.
export const SENIORITIES = [
  'owner',
  'founder',
  'c_suite',
  'partner',
  'vp',
  'head',
  'director',
  'manager',
  'senior',
  'junior',
  'entry',
  'intern',
] as const;

export const isSeniority = (value: string): value is (typeof SENIORITIES)[number] =>
  (SENIORITIES as readonly string[]).includes(value);
