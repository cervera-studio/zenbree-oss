/** Not a server action: an action can neither stream nor be aborted. */
import { z } from 'zod';
import { authFetch } from '@/lib/api/browser';
import { contractError as contract, responseError, toAppError } from '@/lib/api/error';
import { readFrames } from '@/lib/api/event-stream';
import { toCamelCase } from '@/lib/http-client/case-transform';
import { fail, type Result, succeed } from '@/lib/types';
import { type BusinessProfile, profileSchema } from './schema';

export type StudyInput = { website?: string; description?: string; locale?: string };

export type StudyProgress =
  | { type: 'page'; url: string }
  | { type: 'drafting' }
  | { type: 'offer'; summary: string };

const progressSchemas = {
  page: z.looseObject({ url: z.string() }).transform(({ url }) => ({ type: 'page', url }) as const),
  drafting: z.looseObject({}).transform(() => ({ type: 'drafting' }) as const),
  offer: z
    .looseObject({ summary: z.string() })
    .transform(({ summary }) => ({ type: 'offer', summary }) as const),
};
const profileFrame = z.looseObject({ profile: profileSchema });
const errorFrame = z.looseObject({ code: z.string() });

const contractError = (detail: string) => fail(contract(`/v1/onboarding/profile: ${detail}`));

function isProgress(event: string): event is keyof typeof progressSchemas {
  return Object.hasOwn(progressSchemas, event);
}

function settle(event: string, data: unknown): Result<{ profile: BusinessProfile }> | null {
  if (event === 'profile') {
    const done = profileFrame.safeParse(data);
    return done.success ? succeed({ profile: done.data.profile }) : contractError('bad profile');
  }
  if (event === 'error') {
    // The frame is the error body of a stream already answered 200: its code is what counts.
    return errorFrame.safeParse(data).success
      ? fail(toAppError(200, { detail: data }))
      : contractError('bad error');
  }
  return null;
}

export async function studyCompany(
  input: StudyInput,
  onProgress: (progress: StudyProgress) => void,
  signal: AbortSignal,
): Promise<Result<{ profile: BusinessProfile }>> {
  const res = await authFetch('/v1/onboarding/profile', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
    signal,
  });
  if (!res.ok) return fail(await responseError(res));
  if (!res.body) return contractError('no body');
  for await (const frames of readFrames(res.body)) {
    for (const frame of frames) {
      const data = toCamelCase<unknown>(JSON.parse(frame.data));
      if (isProgress(frame.event)) {
        const progress = progressSchemas[frame.event].safeParse(data);
        if (progress.success) onProgress(progress.data);
      }
      const done = settle(frame.event, data);
      if (done) return done;
    }
  }
  return contractError('the stream ended without a profile');
}
