import 'server-only';

import { z } from 'zod';
import type { Camel } from '@/lib/api/camel';
import type { Schema } from '@/lib/api/schema';
import { getToken } from '@/lib/auth-token';
import { httpClient } from '@/lib/http-client';

const viewSchema = z.looseObject({
  id: z.string(),
  name: z.string(),
  listId: z.string(),
  listName: z.string(),
  tab: z.enum(['all', 'delivered', 'pending', 'no_contact', 'excluded']),
  query: z.string(),
  minFit: z.number().int().nullable(),
  createdAt: z.string(),
}) satisfies z.ZodType<Camel<Schema<'SavedView'>>>;
const listSchema = z.looseObject({
  views: z.array(viewSchema),
}) satisfies z.ZodType<Camel<Schema<'SavedViews'>>>;

export type SavedView = Camel<Schema<'SavedView'>>;

const BASE = '/v1/saved-views';

export async function getViews() {
  return httpClient.get(BASE, { token: await getToken(), schema: listSchema });
}

export async function createView(input: {
  listId: string;
  name: string;
  tab: SavedView['tab'];
  query: string;
  minFit: number | null;
}) {
  return httpClient.post(BASE, input, { token: await getToken(), schema: viewSchema });
}

export async function renameView(id: string, name: string) {
  return httpClient.patch(
    `${BASE}/${encodeURIComponent(id)}`,
    { name },
    { token: await getToken(), schema: viewSchema },
  );
}

export async function deleteView(id: string) {
  return httpClient.delete(`${BASE}/${encodeURIComponent(id)}`, { token: await getToken() });
}
