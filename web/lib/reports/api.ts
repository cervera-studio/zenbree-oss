import 'server-only';

import { z } from 'zod';
import type { Camel } from '@/lib/api/camel';
import type { Schema } from '@/lib/api/schema';
import { getToken } from '@/lib/auth-token';
import { httpClient } from '@/lib/http-client';
import type { Range } from './range';

const list = z.looseObject({ id: z.string(), name: z.string() }).nullable();
const sent = z.looseObject({
  email: z.number(),
  linkedinConnect: z.number(),
  linkedinMessage: z.number(),
});

const reportSchema = z.looseObject({
  window: z.looseObject({ start: z.string(), end: z.string(), timeZone: z.string() }),
  campaigns: z.array(
    z.looseObject({
      sender: z.enum(['native', 'instantly']),
      id: z.string(),
      name: z.string(),
      status: z.string().nullable(),
      list,
      counts: z.looseObject({
        enrolled: z.number(),
        contacted: z.number(),
        sent,
        humanReplies: z.number(),
        positiveReplies: z.number(),
        bounced: z.number().nullable(),
        meetings: z.number().nullable(),
        deals: z.number().nullable(),
      }),
      lineage: z.array(
        z.looseObject({
          list,
          source: z.enum(['onboarding', 'manual', 'csv', 'refill', 'signal']).nullable(),
          signal: z
            .looseObject({ watchlistId: z.string(), watchlistName: z.string(), type: z.string() })
            .nullable(),
          contacted: z.number(),
          humanReplies: z.number(),
          positiveReplies: z.number(),
        }),
      ),
      variants: z.array(
        z.looseObject({
          group: z.enum(['a', 'b', 'outside', 'unknown']),
          enrolled: z.number(),
          contacted: z.number(),
          sent,
          humanReplies: z.number(),
          positiveReplies: z.number(),
          bounced: z.number().nullable(),
        }),
      ),
    }),
  ),
  instantly: z
    .looseObject({
      mode: z.string(),
      completeUntil: z.string().nullable(),
      failingSince: z.string().nullable(),
      lastErrorCode: z.string().nullable(),
    })
    .nullable(),
}) satisfies z.ZodType<CampaignReport>;
export type CampaignReport = Camel<Schema<'CampaignReport'>>;
export type ReportRow = CampaignReport['campaigns'][number];
export type ReportVariant = ReportRow['variants'][number];

export async function getCampaignReport(range: Range, timeZone: string) {
  const query = new URLSearchParams({ from: range.from, to: range.to, tz: timeZone });
  return httpClient.get(`/v1/reports/campaigns?${query}`, {
    token: await getToken(),
    schema: reportSchema,
  });
}
