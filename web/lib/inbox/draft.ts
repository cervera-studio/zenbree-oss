import { z } from 'zod';
import { api } from '@/lib/api/browser';
import type { Camel } from '@/lib/api/camel';
import type { Schema } from '@/lib/api/schema';

const draftSchema = z.object({ draft: z.string() }) satisfies z.ZodType<Camel<Schema<'DraftOut'>>>;

export const regenerateDraft = (id: string) =>
  api(`/v1/inbox/${encodeURIComponent(id)}/draft`, { method: 'POST', schema: draftSchema });
