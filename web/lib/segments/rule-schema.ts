import { z } from 'zod';
import type { Condition, RuleGroup, RuleNode } from './rule';

export function ruleSchemas(object: typeof z.looseObject | typeof z.strictObject) {
  const bound = z.number().nullish();
  const field = z.discriminatedUnion('kind', [
    object({ kind: z.literal('canonical'), key: z.string() }),
    object({ kind: z.literal('column'), list_id: z.string(), key: z.string() }),
  ]);
  const condition = z.discriminatedUnion('type', [
    object({
      type: z.literal('text'),
      field,
      op: z.enum(['eq', 'neq', 'contains', 'not_contains', 'in', 'not_in']),
      values: z.array(z.string()),
    }),
    object({
      type: z.literal('number'),
      field,
      op: z.enum(['gt', 'gte', 'lt', 'lte', 'between']),
      low: bound,
      high: bound,
    }),
    object({
      type: z.literal('fit'),
      op: z.enum(['gte', 'lte', 'between', 'is_missing', 'is_filled']),
      low: bound,
      high: bound,
    }),
    object({ type: z.literal('presence'), field, op: z.enum(['is_missing', 'is_filled']) }),
    object({
      type: z.literal('in_list'),
      list_id: z.string(),
      op: z.enum(['in_list', 'not_in_list']),
    }),
  ]) satisfies z.ZodType<Condition>;
  const group: z.ZodType<RuleGroup> = object({
    type: z.literal('group'),
    op: z.enum(['and', 'or']),
    get children(): z.ZodArray<z.ZodType<RuleNode>> {
      return z.array(z.union([condition, group]));
    },
  });
  return { condition, group };
}
