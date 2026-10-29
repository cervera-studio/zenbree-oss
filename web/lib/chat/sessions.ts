import { z } from 'zod';
import { api } from '@/lib/api/browser';
import { API_ROUTES } from '@/lib/config/api-routes';
import { listsFromSession } from './chat-lists';
import { parseProgress } from './job-progress';
import type { ChatMessage } from './types';

// ponytail: loose shapes; the store's own guards (session-loader, parseSources) do the narrowing.
const sessionSchema = z.looseObject({
  id: z.string(),
  projectId: z.string().nullable(),
  title: z.string().nullable(),
  createdAt: z.string(),
  updatedAt: z.string(),
  pinnedAt: z.string().nullable(),
});

const detailSchema = z.looseObject({
  session: sessionSchema,
  messages: z.array(z.looseObject({ id: z.string(), role: z.enum(['user', 'assistant']) })),
  lists: z.array(
    z.looseObject({
      id: z.string(),
      name: z.string(),
      entity: z.string(),
      rowCount: z.number(),
    }),
  ),
  activeJob: z.looseObject({ id: z.string(), status: z.string() }).nullable(),
  fillCredits: z.number(),
});

export const sessionsApi = {
  get: async (id: string) => {
    const res = await api(API_ROUTES.chat.sessions.get(id), { schema: detailSchema });
    if (!res.success) return res;
    const { session, lists, activeJob, messages, fillCredits } = res.data;
    return {
      ...res,
      data: {
        messages: messages as unknown as ChatMessage[],
        lists: listsFromSession(lists),
        title: session.title,
        jobId: activeJob?.id ?? null,
        progress: parseProgress(activeJob),
        fillCredits,
        projectId: session.projectId,
      },
    };
  },
  // The sidebar stream (#342) carries the change back: no local state to patch.
  pin: (id: string, pinned: boolean) =>
    api(API_ROUTES.chat.sessions.get(id), { method: 'PATCH', body: { pinned } }),
  remove: (id: string) => api(API_ROUTES.chat.sessions.get(id), { method: 'DELETE' }),
  cancelJob: (jobId: string) =>
    api(API_ROUTES.jobs.cancel(jobId), {
      method: 'POST',
      schema: z.looseObject({ status: z.string() }),
    }),
};
