import 'server-only';

// The campaign builder's own calls (#217), apart from `api.ts` so the C1/C3 pages can change it freely.
import { getToken } from '@/lib/auth-token';
import { httpClient } from '@/lib/http-client';
import { previewSchema } from './api';
import { type CampaignField, fieldCatalogSchema } from './fields';
import type { Step } from './steps';

const at = (id: string, path: string) => `/v1/campaigns/${encodeURIComponent(id)}${path}`;

/** The saved steps for the first 10 people: the person picker's choices. */
export async function getBuilderPreview(id: string) {
  return httpClient.get(at(id, '/preview?limit=10'), {
    token: await getToken(),
    schema: previewSchema,
  });
}

export async function getFieldCatalog(id: string) {
  return httpClient.get(at(id, '/fields'), {
    token: await getToken(),
    schema: fieldCatalogSchema,
  });
}

/** Unsaved steps and fields, rendered for the same people; nothing is stored. */
export async function livePreview(
  id: string,
  steps: Pick<Step, 'channel' | 'subject' | 'body' | 'threaded' | 'b'>[],
  fields: CampaignField[],
) {
  return httpClient.post(
    at(id, '/preview'),
    { steps, fields, limit: 10 },
    { token: await getToken(), schema: previewSchema },
  );
}
