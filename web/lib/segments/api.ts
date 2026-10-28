import 'server-only';

import { z } from 'zod';
import type { Schema } from '@/lib/api/schema';
import { getToken } from '@/lib/auth-token';
import { httpClient } from '@/lib/http-client';
import { getProjects } from '@/lib/projects/server';
import { type Result, succeed } from '@/lib/types';
import type { FieldList, ListColumn } from './fields';
import type { Condition, Entity, RuleGroup } from './rule';
import { ruleSchemas } from './rule-schema';

const entity = z.enum(['person', 'company']);
const { condition, group: ruleGroup } = ruleSchemas(z.looseObject);

const problem = z.looseObject({
  path: z.string(),
  reason: z.enum(['list_not_found', 'column_not_found']),
  list_id: z.string(),
  key: z.string().nullable(),
}) satisfies z.ZodType<Schema<'RuleProblem'>>;

const segmentSchema = z.looseObject({
  id: z.string(),
  name: z.string(),
  entity,
  rule: ruleGroup,
  version: z.number(),
  published_at: z.string().nullable(),
  created_at: z.string(),
  created_by: z.string().nullable(),
  members: z.number(),
  problems: z.array(problem),
}) satisfies z.ZodType<Schema<'AudienceSegment'>>;
export type Segment = z.infer<typeof segmentSchema>;
export type RuleProblem = z.infer<typeof problem>;

const memberRow = z.looseObject({
  id: z.string(),
  list_id: z.string(),
  cells: z.record(z.string(), z.unknown()),
  relevance_score: z.number().nullable(),
}) satisfies z.ZodType<Schema<'MemberRow'>>;
export type MemberRow = z.infer<typeof memberRow>;

const previewSchema = z.looseObject({
  count: z.number(),
  pending: z.number(),
  unidentified_rows: z.number(),
  linking_rows: z.number(),
  sample: z.array(z.looseObject({ identity_id: z.string(), row: memberRow.nullable() })),
  joining: z.number().nullish(),
  leaving: z.number().nullish(),
  max_credits: z.number().nullish(),
}) satisfies z.ZodType<Schema<'SegmentPreview'>>;
export type SegmentPreview = z.infer<typeof previewSchema>;

const publishedSchema = z.looseObject({
  segment: segmentSchema,
  applied: z.looseObject({
    joined: z.number(),
    entered: z.number(),
    exited: z.number(),
    merged: z.number(),
    members: z.number(),
  }),
  max_credits: z.number(),
}) satisfies z.ZodType<Schema<'Published'>>;

const editedSchema = z.looseObject({
  segment: segmentSchema,
  joining: z.number(),
  leaving: z.number(),
  max_credits: z.number(),
}) satisfies z.ZodType<Schema<'Edited'>>;

const membersSchema = z.looseObject({
  members: z.array(
    z.looseObject({
      identity_id: z.string(),
      episode: z.number(),
      entered_at: z.string(),
      row: memberRow.nullable(),
    }),
  ),
  next_cursor: z.string().nullable(),
}) satisfies z.ZodType<Schema<'Members'>>;
export type Members = z.infer<typeof membersSchema>;

const cellSource = z.looseObject({
  url: z.string(),
  title: z.string(),
}) satisfies z.ZodType<Schema<'CellSource'>>;

const evidence = z.looseObject({
  list_id: z.string(),
  list_name: z.string(),
  row_id: z.string(),
  hit: z.boolean(),
  value: z.unknown(),
  at: z.string().nullish(),
  source: cellSource.nullish(),
  status: z.string().nullish(),
  relevance_score: z.number().nullish(),
});
export type Evidence = z.infer<typeof evidence>;
export type WhyNode = {
  path: string;
  type: 'group' | 'condition';
  value: boolean;
  op?: 'and' | 'or' | null;
  children: WhyNode[];
  condition?: Condition | null;
  rows: Evidence[];
};
const whyNode: z.ZodType<WhyNode> = z.looseObject({
  path: z.string(),
  type: z.enum(['group', 'condition']),
  value: z.boolean(),
  op: z.enum(['and', 'or']).nullish(),
  get children(): z.ZodArray<z.ZodType<WhyNode>> {
    return z.array(whyNode);
  },
  condition: condition.nullish(),
  rows: z.array(evidence),
});
const whySchema = z.looseObject({
  identity_id: z.string(),
  matches: z.boolean(),
  pending: z.boolean(),
  member: z.boolean(),
  rule: whyNode,
  problems: z.array(problem),
}) satisfies z.ZodType<Schema<'Why'>>;
export type Why = z.infer<typeof whySchema>;

const listsSchema = z.looseObject({
  lists: z.array(z.looseObject({ id: z.string(), name: z.string(), entity })),
});
const listColumnsSchema = z.looseObject({
  columns: z.array(
    z.looseObject({
      key: z.string(),
      label: z.string(),
      kind: z.enum(['ai', 'code', 'score', 'static']),
    }),
  ),
});

const BASE = '/v1/segments';
const at = (id: string) => `${BASE}/${encodeURIComponent(id)}`;
const options = async <T>(schema: z.ZodType<T>) => ({
  token: await getToken(),
  schema,
  camel: false,
});

export async function listSegments() {
  return httpClient.get(BASE, await options(z.looseObject({ segments: z.array(segmentSchema) })));
}

export async function getSegment(id: string) {
  return httpClient.get(at(id), await options(segmentSchema));
}

export async function createSegment(body: { name: string; entity: Entity; rule: RuleGroup }) {
  return httpClient.post(BASE, body, await options(segmentSchema));
}

export async function editSegment(
  id: string,
  body: { version: number; name?: string; rule?: RuleGroup; include_new?: boolean },
) {
  return httpClient.patch(at(id), body, await options(editedSchema));
}

export async function deleteSegment(id: string) {
  return httpClient.delete(at(id), { token: await getToken() });
}

export async function publishSegment(id: string, includeCurrent: boolean) {
  return httpClient.post(
    `${at(id)}/publish`,
    { include_current: includeCurrent },
    await options(publishedSchema),
  );
}

export async function refreshSegment(id: string) {
  return httpClient.post(`${at(id)}/refresh`, undefined, await options(publishedSchema));
}

export async function previewSegment(body: {
  entity: Entity;
  rule: RuleGroup;
  segment_id?: string;
}) {
  return httpClient.post(`${BASE}/preview`, body, await options(previewSchema));
}

export async function getMembers(id: string, after: string | null, limit = 50) {
  return httpClient.get(`${at(id)}/members`, {
    ...(await options(membersSchema)),
    params: { limit, ...(after && { after }) },
  });
}

export async function getWhy(id: string, identityId: string) {
  return httpClient.get(
    `${at(id)}/members/${encodeURIComponent(identityId)}/why`,
    await options(whySchema),
  );
}

export async function getFieldLists(): Promise<Result<FieldList[]>> {
  const projects = await getProjects();
  if (!projects.success) return projects;
  const project = projects.data.active?.id;
  if (!project) return succeed([]);
  const lists = await httpClient.get(
    `/v1/lists?project_id=${encodeURIComponent(project)}`,
    await options(listsSchema),
  );
  if (!lists.success) return lists;
  return succeed(lists.data.lists.map(({ id, name, entity }) => ({ id, name, entity })));
}

export async function getListColumns(listId: string): Promise<Result<ListColumn[]>> {
  const list = await httpClient.get(
    `/v1/lists/${encodeURIComponent(listId)}`,
    await options(listColumnsSchema),
  );
  if (!list.success) return list;
  return succeed(
    list.data.columns.flatMap((c) => (c.kind === 'score' ? [] : [{ key: c.key, label: c.label }])),
  );
}

export async function getColumnsOf(
  listIds: readonly string[],
): Promise<Record<string, ListColumn[]>> {
  const read = await Promise.all(
    listIds.map(async (id) => [id, await getListColumns(id)] as const),
  );
  return Object.fromEntries(read.flatMap(([id, r]) => (r.success ? [[id, r.data]] : [])));
}
